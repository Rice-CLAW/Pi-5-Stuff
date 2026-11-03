# EMG Classification

Train and evaluate SVM, random forest, and LDA classifiers on recorded EMG data. By default, the scripts train a separate model for each participant and date session, then test it on recordings held out from that same session.

Run these commands from `ml_classification/`. Create the shared Python environment once.

```bash
python3 -m venv svm/.venv
svm/.venv/bin/python -m pip install numpy scikit-learn joblib
```

The training scripts use that environment automatically.

```bash
python3 svm/train_svm.py --dataset gestures
python3 random_forest/train_rf.py --dataset gestures
python3 lda/train_lda.py --dataset gestures
```

Use `--dataset mendeley` to switch datasets. The default dataset paths are:

| Dataset | Path |
| --- | --- |
| Gestures | `EMG_data/gestures_csv/` |
| Mendeley | `EMG_data/mendeley_data/EMG_DATA/data/csv_data/` |

Use `--max-sessions 1` for a quick run. Use the same `--seed` and session options across classifiers to compare them on the same split. The default feature set is `hudgins`. Run any training script with `--help` to see the available options.

Each classifier saves its models and a `summary.csv` under its `results_<dataset>/` folder. `emg_common.py` handles loading, splitting, and training. `emg_features.py` defines the features, and `convert_gestures.py` converts the original gesture recordings into CSVs.
