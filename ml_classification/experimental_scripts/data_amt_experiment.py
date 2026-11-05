
import csv
import numpy as np
from sklearn.metrics import accuracy_score
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from emg_common import ROOT, records, model
from emg_features import extract

# Settings
SESSION = "subject_01/2016-03-22"
TRAIN_SECONDS = [3, 4, 5]
WINDOW_MS = [100, 150, 250]
SAMPLE_RATE = 1000
SEED = 42

# Load one participant's two recordings
path = ROOT / "EMG_data/gestures_csv"
data = [r for r in records("gestures", path) if r[1] == SESSION]
if not data:
    raise ValueError("Session not found")

recordings = sorted({r[2] for r in data})
if len(recordings) != 2:
    raise ValueError("Expected exactly two recordings for this session")

train_recording, test_recording = recordings

# Read each gesture file and preserve segment boundaries
def read_segments(record):
    with open(record[0], newline="") as f:
        rows = list(csv.DictReader(f))
    groups = {}
    for row in rows:
        signal = [float(row[f"Channel_{i}"]) for i in range(1, 9)]
        groups.setdefault(row["Segment"], []).append(signal)
    return [np.array(group) for group in groups.values()]

train_data = [(r[3], read_segments(r)) for r in data if r[2] == train_recording]
test_data = [(r[3], read_segments(r)) for r in data if r[2] == test_recording]

# Create overlapping windows without crossing segment boundaries
def make_features(dataset, window, training_seconds=None):
    X, y = [], []
    for label, segments in dataset:
        for segment in segments:
            if training_seconds is not None:
                limit = round(training_seconds * SAMPLE_RATE / len(segments))
                if limit > len(segment):
                    raise ValueError(f"Not enough data for gesture {label}: requested {training_seconds} seconds")
                segment = segment[:limit]
            for start in range(0, len(segment) - window + 1, max(1, window // 2)):
                X.append(extract(segment[start:start + window], "hudgins", 1e-5))
                y.append(label)
    return np.array(X), np.array(y)

# Compare models for each training duration and window size
print("Session:", SESSION)
print("Training recording:", train_recording)
print("Testing recording:", test_recording)
print("Training seconds per gesture:", TRAIN_SECONDS)
print("Classification windows (ms):", WINDOW_MS)
print()
print(f"{'Train (s)':>9} {'Window (ms)':>12} {'Train windows':>14} {'Test windows':>13} {'LDA':>9} {'RF':>9} {'SVM':>9}")
print("-" * 82)

for seconds in TRAIN_SECONDS:
    for milliseconds in WINDOW_MS:
        window = round(milliseconds * SAMPLE_RATE / 1000)
        X_train, y_train = make_features(train_data, window, seconds)
        X_test, y_test = make_features(test_data, window)
        scores = []

        for kind in ["lda", "rf", "svm"]:
            clf = model(kind, SEED)
            if kind == "rf":
                clf.set_params(n_jobs=1)
            clf.fit(X_train, y_train)
            scores.append(accuracy_score(y_test, clf.predict(X_test)))

        print(f"{seconds:>9.1f} {milliseconds:>12} {len(X_train):>14} {len(X_test):>13} {scores[0]:>8.1%} {scores[1]:>9.1%} {scores[2]:>9.1%}", flush=True)
