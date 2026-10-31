# CLAW EMG SVM baseline

Linux command-line training and testing for Mendeley dataset [m6t78vngbt, version 1](https://data.mendeley.com/datasets/m6t78vngbt/1).

## Setup on the Pi

Place `train_svm.py` and `requirements.txt` in `/home/wristpower/ml_classification/svm`.

```bash
cd ~/ml_classification/svm
sudo apt install -y python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Train and test

```bash
python train_svm.py
```

The default data path is `/home/wristpower/ml_classification/EMG_data/mendeley_data/EMG_DATA/data/csv_data`.
Override it with `--data-dir /path/to/csv_data`.

- Filenames supply subject, repetition and gesture IDs: `S10_R1_G1.csv`.
- CSVs must contain `Channel_1` through `Channel_8` in order.
- Uses 50-sample windows (250 ms at the documented 200 Hz), stepping by 25 samples.
- Features: mean absolute value, RMS, standard deviation and mean absolute successive difference (mean waveform length), per channel: 32 features total.
- StandardScaler followed by RBF SVM, C=1, gamma=scale. Scaling is fitted on training data only.
- Default: repetitions 1–4 train; repetitions 5–6 test, across all available subjects. No recording contributes windows to both sets. According to the dataset description, the final two repetitions were collected standing; this split also tests that acquisition-condition change.
- Alternative: hold out subjects 10 and 11 entirely:

```bash
python train_svm.py --split subject --test-subjects 10 11 --output-dir results_subject
```

This measures generalization to unseen participants. The default repetition split measures new trials from participants included in training. These are different evaluation questions.

## Results

The script prints window-level accuracy, precision/recall/F1 and a confusion matrix. It saves these in `results/`:

| File | Contents |
| --- | --- |
| `svm_model.joblib` | Fitted scaler/SVM pipeline and feature settings |
| `metrics.json` | Metrics, parameters and scikit-learn version |
| `report.txt` | Text classification report and confusion matrix |
| `split_manifest.csv` | Every source recording and its train/test assignment |
| `test_predictions.csv` | Test window locations, labels and predictions |

Run `source .venv/bin/activate` again after opening a new SSH session. Each run overwrites files in its output directory; use a different `--output-dir` to preserve experiments.

## Interpretation

The uploaded ZIP contains 420 CSVs from subjects 1–5 and 7–11 (no subject 6), seven gesture IDs and six repetitions each. Gesture names are not included in the CSV files, so the script retains G1–G7 rather than inventing anatomical labels. All rows in each recording inherit its filename's gesture label; this baseline does not detect or remove transitions/rest periods within recordings. No additional signal filtering or hyperparameter tuning is applied.

Overlapping windows within a split are correlated; treat window accuracy as a baseline, not independent repeated trials. Keep test data out of parameter tuning. These raw Myo recordings differ from MyoWare envelope outputs; a trained model here is not directly validated for your orthosis sensors or pediatric users.

For future inference, use the saved pipeline with the same channel order and feature extraction. Load joblib files only from trusted sources and use matching package versions.

References: [SVC](https://scikit-learn.org/stable/modules/generated/sklearn.svm.SVC.html), [avoiding preprocessing leakage](https://scikit-learn.org/stable/common_pitfalls.html).
