#!/usr/bin/env python3
"""Train a window-feature SVM; hold out complete repetitions or subjects."""
import argparse
import csv
import json
import re
from pathlib import Path
from time import perf_counter
import joblib
import numpy as np
import sklearn
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

DEFAULT_DATA = Path.home() / 'ml_classification/EMG_data/mendeley_data/EMG_DATA/data/csv_data'
CHANNELS = [f'Channel_{i}' for i in range(1, 9)]

def features(window):
    """MAV, RMS, standard deviation, mean waveform length, in that order."""
    return np.concatenate([np.abs(window).mean(axis=0),
                           np.sqrt((window ** 2).mean(axis=0)),
                           window.std(axis=0),
                           np.abs(np.diff(window, axis=0)).mean(axis=0)])

def load_recording(path):
    with path.open(newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != CHANNELS:
            raise ValueError(f'{path}: expected columns {CHANNELS}, got {reader.fieldnames}')
        data = np.array([[float(row[c]) for c in CHANNELS] for row in reader])
    if data.ndim != 2 or data.shape[1] != 8 or not np.isfinite(data).all():
        raise ValueError(f'{path}: invalid or nonfinite data')
    return data

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--output-dir', type=Path, default=Path('results'))
    parser.add_argument('--split', choices=['repetition', 'subject'], default='repetition')
    parser.add_argument('--test-reps', type=int, nargs='+', default=[5, 6])
    parser.add_argument('--test-subjects', type=int, nargs='+', default=[10, 11])
    parser.add_argument('--window', type=int, default=50, help='Samples; 50 = 250 ms at 200 Hz')
    parser.add_argument('--step', type=int, default=25, help='Samples between windows')
    args = parser.parse_args()
    if args.window < 2 or args.step < 1:
        parser.error('window must be >= 2 and step >= 1')
    files = sorted(args.data_dir.glob('subject_*/*.csv'))
    if not files:
        parser.error(f'No CSV recordings found in {args.data_dir}')
    xs, ys, tests, records, manifest = [], [], [], [], []
    for path in files:
        match = re.fullmatch(r'S(\d+)_R(\d+)_G(\d+)\.csv', path.name)
        if not match:
            raise ValueError(f'Unexpected filename: {path}')
        subject, repetition, gesture = map(int, match.groups())
        data = load_recording(path)
        if len(data) < args.window:
            raise ValueError(f'{path}: recording shorter than window')
        test = repetition in args.test_reps if args.split == 'repetition' else subject in args.test_subjects
        starts = range(0, len(data) - args.window + 1, args.step)
        count = 0
        for start in starts:
            xs.append(features(data[start:start + args.window]))
            ys.append(gesture)
            tests.append(test)
            records.append((str(path.relative_to(args.data_dir)), start))
            count += 1
        manifest.append(dict(file=str(path.relative_to(args.data_dir)), subject=subject,
                             repetition=repetition, gesture=gesture, samples=len(data),
                             windows=count, split='test' if test else 'train'))
    x, y, test = np.array(xs), np.array(ys), np.array(tests)
    train = ~test
    labels = np.unique(y)
    if not test.any() or not train.any():
        parser.error('Selected split must contain both training and test recordings')
    if set(y[train]) != set(labels) or set(y[test]) != set(labels):
        parser.error('Every gesture must occur in both training and test sets')
    print(f'Recordings: {len(files)}; subjects: {sorted({m["subject"] for m in manifest})}')
    print(f'Features per window: {x.shape[1]}; train windows: {train.sum()}; test windows: {test.sum()}')
    print(f'Split: {args.split}; test reps: {args.test_reps}' if args.split == 'repetition'
          else f'Split: subject; test subjects: {args.test_subjects}')
    model = make_pipeline(StandardScaler(), SVC(kernel='rbf', C=1.0, gamma='scale'))
    start = perf_counter()
    model.fit(x[train], y[train])
    train_seconds = perf_counter() - start
    pred = model.predict(x[test])
    accuracy = accuracy_score(y[test], pred)
    names = [f'G{i}' for i in labels]
    report = classification_report(y[test], pred, labels=labels, target_names=names, zero_division=0)
    cm = confusion_matrix(y[test], pred, labels=labels)
    print(f'Training time: {train_seconds:.2f} s\nTest window accuracy: {accuracy:.2%}\n{report}')
    print('Confusion matrix: rows = true, columns = predicted; order:', names)
    print(cm)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    settings = dict(sample_rate_hz=200, window=args.window, step=args.step,
                    channels=CHANNELS, feature_order=['MAV', 'RMS', 'STD', 'mean_waveform_length'],
                    labels=[int(i) for i in labels], split=args.split,
                    test_reps=args.test_reps, test_subjects=args.test_subjects,
                    sklearn_version=sklearn.__version__)
    joblib.dump(dict(pipeline=model, settings=settings), args.output_dir / 'svm_model.joblib')
    metrics = dict(settings=settings, accuracy=float(accuracy), train_seconds=train_seconds,
                   train_windows=int(train.sum()), test_windows=int(test.sum()),
                   confusion_matrix=cm.tolist(), report=classification_report(
                       y[test], pred, labels=labels, target_names=names, zero_division=0, output_dict=True))
    (args.output_dir / 'metrics.json').write_text(json.dumps(metrics, indent=2))
    (args.output_dir / 'report.txt').write_text(report + '\n' + str(cm) + '\n')
    with (args.output_dir / 'split_manifest.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    with (args.output_dir / 'test_predictions.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['file', 'start_sample', 'true_gesture', 'predicted_gesture'])
        for index, prediction in zip(np.flatnonzero(test), pred):
            writer.writerow([*records[index], int(y[index]), int(prediction)])
    print(f'Saved model, metrics, split manifest and predictions to {args.output_dir.resolve()}')

if __name__ == '__main__':
    main()
