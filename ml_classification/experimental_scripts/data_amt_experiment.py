
import sys
from pathlib import Path
import numpy as np
from sklearn.metrics import accuracy_score
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from emg_common import ROOT, records, windows, model

# Settings
SUBJECT = "subject_1"
TRAIN_REPETITIONS = [1, 2, 3, 4, 5]
WINDOW_SAMPLES = [25, 50, 100, 150, 250]
TEST_REPETITION = 6
SEED = 42

# Load one participant's recordings
path = ROOT / "EMG_data/mendeley_data/EMG_DATA/data/csv_data"
data = [r for r in records("mendeley", path) if r[1] == SUBJECT]

if not data:
    raise ValueError("Subject not found")

available = {int(r[2]) for r in data}
if TEST_REPETITION not in available or any(n < 1 or n >= TEST_REPETITION for n in TRAIN_REPETITIONS):
    raise ValueError("Training repetitions must be 1–5; R6 is reserved for testing")

# Compare repetitions and classification windows
print("Subject:", SUBJECT)
print("Training: R1 through Rn; Testing: R6")
print("Window sizes are measured in samples")
print(f"{'Reps':>5} {'Window':>8} {'Train windows':>14} {'Test windows':>13} {'LDA':>9} {'RF':>9} {'SVM':>9}", flush=True)
print("-" * 78)

for window_size in WINDOW_SAMPLES:
    features = {}

    for r in data:
        features[(int(r[2]), r[3])] = windows(r, "mendeley", window_size, max(1, window_size // 2), "hudgins", 1.0)

    for n in TRAIN_REPETITIONS:
        train = [(x, label) for (rep, label), x in features.items() if rep <= n]
        test = [(x, label) for (rep, label), x in features.items() if rep == TEST_REPETITION]

        X_train = np.concatenate([x for x, label in train])
        y_train = np.concatenate([np.full(len(x), label) for x, label in train])
        X_test = np.concatenate([x for x, label in test])
        y_test = np.concatenate([np.full(len(x), label) for x, label in test])

        scores = []

        for kind in ["lda", "rf", "svm"]:
            clf = model(kind, SEED)

            if kind == "rf":
                clf.set_params(n_jobs=1)

            clf.fit(X_train, y_train)
            scores.append(accuracy_score(y_test, clf.predict(X_test)))

        print(f"{n:>5} {window_size:>8} {len(X_train):>14} {len(X_test):>13} {scores[0]:>8.1%} {scores[1]:>9.1%} {scores[2]:>9.1%}", flush=True)
