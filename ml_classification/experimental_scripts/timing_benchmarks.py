
import time
import numpy as np
from sklearn.metrics import accuracy_score
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from emg_common import ROOT, records, windows, model

# Settings
SESSION = "subject_01/2016-03-22"
WINDOW_SIZE = 250
STEP_SIZE = 125
NUM_PREDICTIONS = 300
SEED = 42

# Load EMG data
path = ROOT / "EMG_data/gestures_csv"
data = records("gestures", path)
data = [r for r in data if r[1] == SESSION]

if not data:
    raise ValueError("Participant/session not found")

recordings = sorted({r[2] for r in data})
rng = np.random.default_rng(SEED)
train_recording = rng.choice(recordings)

print("Participant:", SESSION)
print("Training recording:", train_recording)
print("Testing recordings:", [r for r in recordings if r != train_recording], flush=True)

# Extract features
X_train, y_train = [], []
X_test, y_test = [], []

print("Extracting EMG features...", flush=True)

for record in data:
    features = windows(record, "gestures", WINDOW_SIZE, STEP_SIZE, "hudgins", 1e-5)
    label = record[3]
    recording = record[2]

    if recording == train_recording:
        X_train.extend(features)
        y_train.extend([label] * len(features))
    else:
        X_test.extend(features)
        y_test.extend([label] * len(features))

X_train = np.array(X_train)
X_test = np.array(X_test)
y_train = np.array(y_train)
y_test = np.array(y_test)

print("Training windows:", len(X_train))
print("Testing windows:", len(X_test), flush=True)

# Train and test models
models = [("LDA", "lda"), ("Random Forest", "rf"), ("SVM", "svm")]
results = []

for name, kind in models:
    print(f"\nTesting {name}...", flush=True)
    clf = model(kind, SEED)

    if kind == "rf":
        clf.set_params(n_jobs=1)

    start = time.perf_counter()
    clf.fit(X_train, y_train)
    train_time = time.perf_counter() - start

    predictions = clf.predict(X_test)
    accuracy = accuracy_score(y_test, predictions)

    for i in range(20):
        index = i % len(X_test)
        clf.predict(X_test[index:index + 1])

    times = []
    for i in range(NUM_PREDICTIONS):
        index = i % len(X_test)
        sample = X_test[index:index + 1]
        start = time.perf_counter_ns()
        clf.predict(sample)
        elapsed = (time.perf_counter_ns() - start) / 1_000_000
        times.append(elapsed)

    avg_latency = np.mean(times)
    median_latency = np.median(times)
    p95_latency = np.percentile(times, 95)
    results.append([name, train_time, avg_latency, median_latency, p95_latency, accuracy])

# Print results
print("\n" + "=" * 90)
print("EMG CLASSIFIER TIMING COMPARISON")
print("=" * 90)
print(f"{'Model':<16}{'Train (s)':>12}{'Mean (ms)':>14}{'Median (ms)':>14}{'P95 (ms)':>12}{'Accuracy':>12}")
print("-" * 90)

for r in results:
    print(f"{r[0]:<16}{r[1]:>12.4f}{r[2]:>14.4f}{r[3]:>14.4f}{r[4]:>12.4f}{r[5]:>11.2%}")

print("=" * 90)
