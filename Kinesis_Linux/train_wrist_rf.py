#!/usr/bin/env python3
"""Prompted wrist EMG recording and random forest training on a Linux host."""
import argparse
from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path
import random
import re
import signal
import subprocess
import tempfile
import threading
import time

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix

LABELS = ("rest", "wrist_flexion", "wrist_extension")
PROMPTS = {
    "rest": "Relax your wrist and fingers in a neutral position.",
    "wrist_flexion": "Bend your wrist toward your palm and hold gently.",
    "wrist_extension": "Bend your wrist toward the back of your hand and hold gently.",
}
SAMPLE_RATE = 2048
CHANNELS = 8
BATCH_SIZE = 16
WINDOW = 512  # 250 ms
HOP = 256     # 125 ms
MIN_BATCHES = 24  # 75% of a 32-batch feature window
FEATURE_NAMES = ("mean_absolute_value", "rms", "waveform_length")
TRAIN_REPS = 3
TOTAL_REPS = 4


@dataclass
class Batch:
    received_at: float
    sequence: int
    samples: list


class Stream:
    def __init__(self, command):
        self.command = command
        self.pending = deque(maxlen=512)
        self.condition = threading.Condition()
        self.ended = False
        self.unsupported = False

    def __enter__(self):
        self.process = subprocess.Popen(
            self.command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", start_new_session=True,
        )
        self.reader = threading.Thread(target=self.read_lines, daemon=True)
        self.reader.start()
        return self

    def read_lines(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                    if event.get("event") == "raw_emg_payload":
                        with self.condition:
                            self.unsupported = True
                            self.condition.notify_all()
                        continue
                    if event.get("event") != "emg_batch":
                        continue
                    batch = Batch(
                        float(event["received_at"]), int(event["sequence"]), event["samples"],
                    )
                except (AttributeError, KeyError, TypeError, ValueError):
                    continue
                with self.condition:
                    self.pending.append(batch)
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.ended = True
                self.condition.notify_all()

    def next_batch(self, deadline):
        with self.condition:
            while not self.pending:
                if self.ended:
                    raise RuntimeError("Kinesis stopped streaming. Check the Kinesis message above.")
                if self.unsupported:
                    raise RuntimeError("Kinesis received an EMG format it cannot decode.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)
            return self.pending.popleft()

    def __exit__(self, *_):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.reader.join(timeout=1)
        self.process.stdout.close()


def window_features(window, sequences):
    """Compute features on observed samples and skip differences across gaps."""
    signal = np.asarray(window, dtype=np.float64)
    signal = signal - signal.mean(axis=0)
    mav = np.abs(signal).mean(axis=0)
    rms = np.sqrt(np.square(signal).mean(axis=0))
    adjacent = np.ones(len(signal) - 1, dtype=bool)
    adjacent[BATCH_SIZE - 1::BATCH_SIZE] = np.diff(sequences) == 1
    # Normalize to the nominal window length so missing packets do not lower WL.
    wl = np.abs(np.diff(signal, axis=0)[adjacent]).mean(axis=0) * (WINDOW - 1)
    return np.column_stack((mav, rms, wl)).ravel()


def feature_matrix(samples, sequences):
    if not len(sequences):
        return np.empty((0, CHANNELS * len(FEATURE_NAMES)))
    window_batches = WINDOW // BATCH_SIZE
    hop_batches = HOP // BATCH_SIZE
    rows = []
    # Anchor each window to a range of device sequence numbers, not host arrival
    # time or the count of received batches. Missing batches stay missing.
    first = sequences[0]
    for bucket in np.unique((sequences - first) // hop_batches):
        start = first + bucket * hop_batches
        end = start + window_batches
        if end - 1 > sequences[-1]:
            break
        left, right = np.searchsorted(sequences, (start, end))
        if right - left >= MIN_BATCHES:
            rows.append(window_features(
                samples[left * BATCH_SIZE:right * BATCH_SIZE], sequences[left:right],
            ))
    return np.asarray(rows).reshape(-1, CHANNELS * len(FEATURE_NAMES))


def capture(stream, seconds):
    print("RECORDING. Hold the position.", flush=True)
    start = time.monotonic()
    end = start + seconds
    data, sequences, times = [], [], []
    while time.monotonic() < end:
        batch = stream.next_batch(end)
        if batch is None:
            break
        if not start <= batch.received_at < end:
            continue
        if sequences and batch.sequence <= sequences[-1]:
            continue
        try:
            values = np.asarray(batch.samples, dtype=np.uint16)
        except (TypeError, ValueError, OverflowError):
            continue
        if values.shape != (BATCH_SIZE, CHANNELS):
            continue
        data.append(values)
        sequences.append(batch.sequence)
        times.append(batch.received_at - start)
    print("STOP. Relax your wrist.", flush=True)
    samples = np.concatenate(data, axis=0) if data else np.empty((0, CHANNELS), dtype=np.uint16)
    return samples, np.asarray(sequences, dtype=np.int64), np.asarray(times)


def collect(args, directory):
    repetitions = []
    order = random.Random(args.seed)
    command = [str(args.kinesis.resolve()), "listen", "--address", args.address, "--raw-emg"]
    print(f"Saving to {directory}")
    print("Keep the band in the same position. Return to neutral between repetitions.")
    with Stream(command) as stream:
        print("Waiting for the first decoded EMG batch...", flush=True)
        if stream.next_batch(time.monotonic() + 75) is None:
            raise RuntimeError("No decoded EMG arrived in 75 seconds. Check the band connection.")
        for repetition in range(1, TOTAL_REPS + 1):
            labels = list(LABELS)
            order.shuffle(labels)
            split = "train" if repetition <= TRAIN_REPS else "test"
            for label in labels:
                while True:
                    print(f"\n{label.replace('_', ' ').upper()} — repetition {repetition}/{TOTAL_REPS} ({split})")
                    print(PROMPTS[label])
                    input("Start in neutral. Press Enter when ready... ")
                    print("Get into position now.", flush=True)
                    time.sleep(args.settle)
                    samples, sequences, times = capture(stream, args.seconds)
                    features = feature_matrix(samples, sequences)
                    received = len(sequences)
                    missing = int(sequences[-1] - sequences[0] + 1 - received) if received else 0
                    print(f"Received {received} batches, {missing} missing within the capture, "
                          f"{len(features)} usable windows.")
                    if len(features):
                        break
                    print("No 250 ms window had at least 24 of its 32 batches. "
                          "This repetition will be retried.")
                path = directory / f"{label}_{repetition:02d}_{split}.npz"
                np.savez_compressed(
                    path, samples=samples, sequences=sequences, received_seconds=times,
                    label=label, repetition=repetition, split=split, sample_rate=SAMPLE_RATE,
                )
                print(f"Saved {len(samples)} samples per channel and {len(features)} usable windows.")
                if len(samples) < args.seconds * SAMPLE_RATE * 0.8:
                    print("Fewer samples arrived than expected. Check the Bluetooth link if this repeats.")
                repetitions.append((label, repetition, split, features))
    return repetitions


def fit(repetitions, directory, seed):
    train = [(label, rows) for label, _, split, rows in repetitions if split == "train"]
    test = [(label, rep, rows) for label, rep, split, rows in repetitions if split == "test"]
    x_train = np.concatenate([rows for _, rows in train])
    y_train = np.concatenate([np.repeat(label, len(rows)) for label, rows in train])
    model = RandomForestClassifier(
        n_estimators=250, min_samples_leaf=2, class_weight="balanced_subsample",
        random_state=seed, n_jobs=2,
    )
    model.fit(x_train, y_train)

    true_windows, predicted_windows, trials = [], [], []
    print("\nHeld-out repetitions")
    for label, repetition, rows in test:
        probabilities = model.predict_proba(rows)
        window_predictions = model.classes_[probabilities.argmax(axis=1)]
        prediction = str(model.classes_[probabilities.mean(axis=0).argmax()])
        true_windows.extend([label] * len(rows))
        predicted_windows.extend(window_predictions.tolist())
        trials.append(dict(label=label, repetition=repetition, predicted=prediction, windows=len(rows)))
        print(f"  {label} #{repetition} → {prediction}")

    trial_true = [trial["label"] for trial in trials]
    trial_predicted = [trial["predicted"] for trial in trials]
    report = {
        "labels": LABELS,
        "train_repetitions": len(train),
        "test_repetitions": len(test),
        "train_windows": len(x_train),
        "test_windows": len(true_windows),
        "window_accuracy": accuracy_score(true_windows, predicted_windows),
        "window_balanced_accuracy": balanced_accuracy_score(true_windows, predicted_windows),
        "repetition_accuracy": accuracy_score(trial_true, trial_predicted),
        "window_confusion_matrix": confusion_matrix(true_windows, predicted_windows, labels=LABELS).tolist(),
        "repetition_confusion_matrix": confusion_matrix(trial_true, trial_predicted, labels=LABELS).tolist(),
        "held_out_repetitions": trials,
    }
    joblib.dump({
        "model": model, "labels": LABELS, "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS, "window_samples": WINDOW, "hop_samples": HOP,
        "min_batches_per_window": MIN_BATCHES, "batch_samples": BATCH_SIZE,
        "feature_names_per_channel": FEATURE_NAMES,
        "preprocessing": "subtract per-channel observed mean; waveform length uses adjacent sample pairs only",
        "scikit_learn_version": sklearn.__version__,
    }, directory / "wrist_rf.joblib")
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    correct = sum(t["label"] == t["predicted"] for t in trials)
    print(f"\nHeld-out repetition accuracy: {correct}/{len(trials)}")
    print(f"Held-out window accuracy: {report['window_accuracy']:.1%}")
    print(f"Model and report saved in {directory}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True, help="band's bonded Bluetooth MAC")
    parser.add_argument("--kinesis", type=Path, default=Path(".venv/bin/kinesis-linux"))
    parser.add_argument("--output", type=Path, default=Path.home() / "wrist_emg_runs",
                        help="directory in which to create a new private recording folder")
    parser.add_argument("--seconds", type=float, default=3, help="seconds recorded per repetition")
    parser.add_argument("--settle", type=float, default=1.5, help="seconds to get into position")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", args.address):
        parser.error("--address must be a Bluetooth MAC such as AA:BB:CC:DD:EE:FF")
    if not np.isfinite(args.seconds) or args.seconds < 1:
        parser.error("--seconds must be at least 1")
    if not np.isfinite(args.settle) or args.settle < 0:
        parser.error("--settle must be nonnegative")
    if not 0 <= args.seed < 2**32:
        parser.error("--seed must be between 0 and 4294967295")
    if not args.kinesis.is_file():
        parser.error(f"Kinesis executable not found: {args.kinesis}")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="wrist-rf-", dir=args.output))
    try:
        fit(collect(args, directory), directory, args.seed)
    except (KeyboardInterrupt, EOFError):
        print(f"\nStopped. Completed recordings are in {directory}.")
        raise SystemExit(130) from None
    except (OSError, RuntimeError) as error:
        raise SystemExit(f"{error}\nCompleted recordings are in {directory}.") from None


if __name__ == "__main__":
    main()
