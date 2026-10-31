#!/usr/bin/env python3
"""Evaluate a separate EMG SVM for every participant."""

import argparse
import csv
import json
import re
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC


DEFAULT_DATA = (
    Path.home()
    / "ml_classification/EMG_data/mendeley_data/EMG_DATA/data/csv_data"
)

CHANNELS = [f"Channel_{i}" for i in range(1, 9)]
FEATURE_SETS = ["baseline", "hudgins", "tdar", "relative_tdar"]


def load_recording(path):
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)

        if reader.fieldnames != CHANNELS:
            raise ValueError(f"Unexpected columns: {path}")

        data = np.array(
            [[float(row[c]) for c in CHANNELS] for row in reader],
            dtype=float,
        )

    if (
        data.ndim != 2
        or data.shape[1] != 8
        or not np.isfinite(data).all()
    ):
        raise ValueError(f"Invalid recording: {path}")

    return data


def extract_features(window, feature_set, threshold):
    mav = np.abs(window).mean(axis=0)
    rms = np.sqrt((window**2).mean(axis=0))
    difference = np.diff(window, axis=0)
    wl = np.abs(difference).sum(axis=0)

    if feature_set == "baseline":
        return np.concatenate([
            mav,
            rms,
            window.std(axis=0),
            np.abs(difference).mean(axis=0),
        ])

    zc = (
        (window[:-1] * window[1:] < 0)
        & (np.abs(difference) >= threshold)
    ).sum(axis=0)

    left = window[1:-1] - window[:-2]
    right = window[1:-1] - window[2:]

    ssc = (
        (left * right > 0)
        & (
            (np.abs(left) >= threshold)
            | (np.abs(right) >= threshold)
        )
    ).sum(axis=0)

    hudgins = np.concatenate([mav, wl, zc, ssc])

    if feature_set == "hudgins":
        return hudgins

    centered = window - window.mean(axis=0)
    normalized = centered / np.maximum(centered.std(axis=0), 1e-8)
    ar = []

    for channel in range(8):
        predictors = np.column_stack([
            normalized[4 - lag:len(window) - lag, channel]
            for lag in range(1, 5)
        ])
        target = normalized[4:, channel]
        ar.extend(
            np.linalg.lstsq(predictors, target, rcond=None)[0]
        )

    if feature_set == "tdar":
        return np.concatenate([hudgins, ar])

    # Experimental relative-amplitude variant.
    return np.concatenate([
        mav / max(mav.sum(), 1e-8),
        wl / max(wl.sum(), 1e-8),
        zc / (len(window) - 1),
        ssc / (len(window) - 2),
        ar,
        [np.log1p(rms.mean())],
    ])


def create_model():
    return make_pipeline(
        StandardScaler(),
        SVC(kernel="rbf", C=1.0, gamma="scale"),
    )


def evaluate_subject(subject, folder, args):
    candidates = (
        FEATURE_SETS
        if args.feature_set == "auto"
        else [args.feature_set]
    )

    recordings = []
    gesture_reps = {}

    for path in sorted(folder.glob("*.csv")):
        match = re.fullmatch(
            r"S(\d+)_R(\d+)_G(\d+)\.csv", path.name
        )

        if match is None:
            raise ValueError(f"Unexpected filename: {path}")

        file_subject, repetition, gesture = map(int, match.groups())

        if file_subject != subject:
            raise ValueError(f"Subject mismatch: {path}")

        data = load_recording(path)

        if len(data) < args.window:
            raise ValueError(f"Recording too short: {path}")

        recordings.append((path, repetition, gesture, data))
        gesture_reps.setdefault(gesture, set()).add(repetition)

    if not recordings:
        raise ValueError(f"No CSV files in {folder}")

    # Require the same six repetition IDs for every gesture.
    rep_sets = list(gesture_reps.values())
    repetitions = sorted(rep_sets[0])

    if len(repetitions) != 6 or any(
        reps != rep_sets[0] for reps in rep_sets
    ):
        raise ValueError(
            f"Subject {subject}: expected the same six "
            "repetitions for every gesture"
        )

    pairs = [(rep, gesture) for _, rep, gesture, _ in recordings]
    if len(pairs) != len(set(pairs)):
        raise ValueError(f"Duplicate recordings for subject {subject}")

    # Reproducible, independent random split for each participant.
    rng = np.random.default_rng(
        np.random.SeedSequence([args.seed, subject])
    )
    shuffled = rng.permutation(repetitions)
    train_reps = sorted(int(r) for r in shuffled[:4])
    test_reps = sorted(int(r) for r in shuffled[4:])

    rows = {name: [] for name in candidates}
    labels, groups, locations = [], [], []

    for path, repetition, gesture, data in recordings:
        for start in range(
            0, len(data) - args.window + 1, args.step
        ):
            window = data[start:start + args.window]

            for name in candidates:
                rows[name].append(
                    extract_features(window, name, args.threshold)
                )

            labels.append(gesture)
            groups.append(repetition)
            locations.append((path.name, start))

    y = np.array(labels)
    groups = np.array(groups)
    train = np.isin(groups, train_reps)
    test = np.isin(groups, test_reps)
    matrices = {name: np.asarray(values) for name, values in rows.items()}

    # Feature selection uses training repetitions only.
    folds = list(
        GroupKFold(n_splits=4).split(
            np.zeros(train.sum()), y[train], groups[train]
        )
    )

    cv_scores = {}

    for name in candidates:
        x_train = matrices[name][train]
        y_train = y[train]
        scores = []

        for fit_indices, validation_indices in folds:
            model = create_model()
            model.fit(
                x_train[fit_indices], y_train[fit_indices]
            )
            prediction = model.predict(x_train[validation_indices])
            scores.append(
                accuracy_score(
                    y_train[validation_indices], prediction
                )
            )

        cv_scores[name] = float(np.mean(scores))

    selected = max(cv_scores, key=cv_scores.get)
    x = matrices[selected]

    model = create_model()
    model.fit(x[train], y[train])
    prediction = model.predict(x[test])

    gestures = sorted(gesture_reps)
    matrix = confusion_matrix(y[test], prediction, labels=gestures)

    recalls = {
        gesture: float(matrix[i, i] / matrix[i].sum())
        for i, gesture in enumerate(gestures)
    }

    result = {
        "subject": subject,
        "train_reps": train_reps,
        "test_reps": test_reps,
        "feature_set": selected,
        "validation_scores": cv_scores,
        "accuracy": float(accuracy_score(y[test], prediction)),
        "gesture_recall": recalls,
        "gesture_order": gestures,
        "confusion_matrix": matrix.tolist(),
        "train_windows": int(train.sum()),
        "test_windows": int(test.sum()),
    }

    output = args.output_dir / f"subject_{subject}"
    output.mkdir(parents=True, exist_ok=True)

    settings = {
        "subject": subject,
        "feature_set": selected,
        "window": args.window,
        "step": args.step,
        "threshold": args.threshold,
        "sample_rate_hz": 200,
        "channels": CHANNELS,
        "seed": args.seed,
        "train_reps": train_reps,
        "test_reps": test_reps,
    }

    joblib.dump(
        {"pipeline": model, "settings": settings},
        output / "svm_model.joblib",
    )
    (output / "metrics.json").write_text(
        json.dumps(result, indent=2)
    )

    with (output / "test_predictions.csv").open(
        "w", newline=""
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "file", "start_sample", "true_gesture", "prediction"
        ])

        for index, predicted in zip(np.flatnonzero(test), prediction):
            writer.writerow([
                *locations[index],
                int(y[index]),
                int(predicted),
            ])

    return result


def print_table(results):
    gestures = sorted({
        gesture
        for result in results
        for gesture in result["gesture_recall"]
    })

    headers = (
        ["Subject", "Train reps", "Test reps", "Features"]
        + [f"G{g}" for g in gestures]
        + ["Overall"]
    )
    rows = []

    for result in results:
        rows.append([
            str(result["subject"]),
            ",".join(map(str, result["train_reps"])),
            ",".join(map(str, result["test_reps"])),
            result["feature_set"],
            *[
                f'{result["gesture_recall"][g]:.1%}'
                if g in result["gesture_recall"] else "-"
                for g in gestures
            ],
            f'{result["accuracy"]:.1%}',
        ])

    # Equal participant weighting, rather than pooling all windows.
    means = []
    for gesture in gestures:
        values = [
            r["gesture_recall"][gesture]
            for r in results
            if gesture in r["gesture_recall"]
        ]
        means.append(f"{np.mean(values):.1%}")

    rows.append([
        "Mean", "-", "-", "-", *means,
        f'{np.mean([r["accuracy"] for r in results]):.1%}',
    ])

    widths = [
        max(len(str(row[i])) for row in [headers] + rows)
        for i in range(len(headers))
    ]

    def line(row):
        return " | ".join(
            str(value).ljust(width)
            for value, width in zip(row, widths)
        )

    separator = "-+-".join("-" * width for width in widths)

    print("\nPER-GESTURE TEST RECALL")
    print("Each G column = percentage of that gesture classified correctly.\n")
    print(line(headers))
    print(separator)
    for row in rows:
        print(line(row))

    print("\nOverall = accuracy across all test windows.")
    print("Mean = equal-weight average across participants.")

    return gestures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("results_all_subjects")
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--window", type=int, default=50)
    parser.add_argument("--step", type=int, default=25)
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument(
        "--feature-set",
        choices=["auto"] + FEATURE_SETS,
        default="auto",
    )
    args = parser.parse_args()

    if (
        args.seed < 0
        or args.window < 6
        or args.step < 1
        or args.threshold < 0
    ):
        parser.error(
            "Require seed >= 0, window >= 6, step >= 1, threshold >= 0"
        )

    subjects = []
    for folder in args.data_dir.glob("subject_*"):
        match = re.fullmatch(r"subject_(\d+)", folder.name)
        if folder.is_dir() and match:
            subjects.append((int(match.group(1)), folder))

    subjects.sort()

    if not subjects:
        parser.error(f"No subject folders found in {args.data_dir}")

    results = []

    for index, (subject, folder) in enumerate(subjects, start=1):
        print(
            f"[{index}/{len(subjects)}] Processing subject {subject}...",
            flush=True,
        )
        results.append(evaluate_subject(subject, folder, args))

    gestures = print_table(results)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with (args.output_dir / "summary.csv").open(
        "w", newline=""
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "subject", "train_reps", "test_reps", "feature_set",
            *[f"G{g}_recall" for g in gestures],
            "overall_accuracy",
        ])

        for result in results:
            writer.writerow([
                result["subject"],
                ",".join(map(str, result["train_reps"])),
                ",".join(map(str, result["test_reps"])),
                result["feature_set"],
                *[
                    result["gesture_recall"].get(g, "")
                    for g in gestures
                ],
                result["accuracy"],
            ])

    (args.output_dir / "all_results.json").write_text(
        json.dumps(results, indent=2)
    )

    print(f"\nSaved results to: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
