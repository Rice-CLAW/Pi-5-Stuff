# CLAW: two-dataset EMG training

## Install

Copy/extract this bundle into `/home/wristpower/ml_classification`. It replaces the three entry scripts, so back up existing scripts first. The original Mendeley CSVs stay at `EMG_data/mendeley_data/EMG_DATA/data/csv_data` and are not included again.

One-time shared environment setup (skip if already installed):

```bash
cd ~/ml_classification
python3 -m venv svm/.venv
svm/.venv/bin/python -m pip install numpy scikit-learn joblib
```

Each entry script re-executes itself using `svm/.venv/bin/python` before importing ML libraries. This has the same interpreter/package effect as sourcing activate, without modifying your parent shell. No manual activation is needed. Missing environment produces an actionable error. `CLAW_PYTHON` can override the path to another **venv** interpreter.

## Dataset selection

```bash
python3 svm/train_svm.py --dataset mendeley
python3 random_forest/train_rf.py --dataset gestures
python3 lda/train_lda.py --dataset gestures
```

Defaults: separate model per participant/date session, randomly held-out complete recordings, seed 42, Hudgins features. The original dataset defaults to 4 train repetitions and 2 test repetitions. The new dataset contains only two source recordings per session, so its default is 1 train recording and 1 test recording. All gesture classes in a selected recording stay on the same side of the split. Gesture 0 is excluded from learning.

Explicit repetition count:

```bash
python3 svm/train_svm.py --dataset mendeley --train-reps 3
```

## How many datasets/sessions train one model?

Here a training dataset means **one participant on one date**. Use `--train-datasets N` to randomly choose N complete sessions for one pooled model; all other sessions are held out for testing. This is a cross-session/user task, not the default personalized task. At least one test session must remain.

```bash
python3 lda/train_lda.py --dataset gestures --train-datasets 10
python3 random_forest/train_rf.py --dataset mendeley --train-datasets 5
```

`--max-sessions N` instead limits the total sessions processed before splitting, useful for quick checks. `--seed 42` reproduces the same splits across classifiers. `--data-dir PATH` overrides the selected dataset's root. These options select one source collection at a time: labels are NOT interchangeable across collections, so they are not silently mixed.

## CSV organization

New collection: `EMG_data/gestures_csv/subject_01/2016-03-22/<recording>_G3.csv`.

Each CSV contains only one gesture from one recording. Dates are preserved independently even for the same participant ID. Columns: `Time_ms, Segment, Recording, Channel_1,...,Channel_8`. Segment IDs preserve contiguous source runs: windows never bridge separate gesture bouts. Time and source recording are retained. Label 0 (unmarked) is saved but not used for training. One source row in subject 34 recording 1 lacks its class label and is excluded/logged in `excluded_rows.json`; no label is guessed. `manifest.json` records source-level counts. Original README is included.

New collection labels, from source README:

| ID | Gesture |
| --- | --- |
| 0 | Unmarked (excluded) |
| 1 | Rest |
| 2 | Fist |
| 3 | Wrist flexion |
| 4 | Wrist extension |
| 5 | Radial deviation |
| 6 | Ulnar deviation |
| 7 | Extended palm (not all subjects) |

Mendeley remains G1–G7; no mapping to these names is assumed.

## Features and sampling

`--feature-set hudgins` (default), `baseline`, `tdar`, or `relative_tdar`. This version uses a fixed chosen feature set; it does not silently switch features between participants or run feature-selection CV. Hudgins = MAV/WL/ZC/SSC. TDAR adds AR4. Relative TDAR is an experimental normalized variant. See `emg_features.py` for exact formulas.

Dataset-specific defaults: Mendeley window 50 rows, step 25, threshold 1; gestures window 250 rows, step 125, threshold 0.00001, in each dataset's native amplitude units. New timestamps are irregular, commonly 1 ms apart; 250 rows is roughly 250 ms but not guaranteed to be exactly 250 ms. Data is preserved without resampling/filtering. Models must use the same acquisition rate and feature configuration during inference. The two datasets' window durations should not be assumed exactly equivalent. Override with `--window`, `--step`, `--threshold` when needed.

## Outputs

Results default to `<classifier folder>/results_<dataset>/`: terminal table and `summary.csv` of per-gesture recall and overall accuracy; `split_manifest.json` for audit; settings; saved model bundle per session (or `pooled/`). Mean row weights each tested session equally. Missing gestures print `-`, not zero. Pooled training fails explicitly if test contains a gesture absent from training. Use `--output-dir` for preserving runs; default output files are overwritten on rerun.

## Reconvert source ZIP (optional)

Already-converted CSVs are included. To regenerate into a new directory:

```bash
python3 convert_gestures.py 'emg+data+for+gestures.zip' EMG_data/gestures_csv_new
```

Source: supplied archive `emg+data+for+gestures.zip`, README cites Lobov et al., *Latent Factors Limiting the Performance of sEMG-Interfaces*, Sensors 2018, 18(4):1122, doi:10.3390/s18041122.
