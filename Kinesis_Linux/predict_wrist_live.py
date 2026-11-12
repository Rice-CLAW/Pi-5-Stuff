#!/usr/bin/env python3
"""Show live wrist predictions from a model saved by train_wrist_rf.py."""
import argparse
from collections import deque
from pathlib import Path
import re
import sys
import time

import joblib
import numpy as np

from train_wrist_rf import (
    BATCH_SIZE, CHANNELS, FEATURE_NAMES, HOP, LABELS, MIN_BATCHES,
    SAMPLE_RATE, WINDOW, Stream, window_features,
)

MAX_SAMPLE_AGE = 0.5
WINDOW_BATCHES = WINDOW // BATCH_SIZE
HOP_BATCHES = HOP // BATCH_SIZE


def load_model(path):
    if path.is_dir():
        path = path / "wrist_rf.joblib"
    artifact = joblib.load(path)
    expected = {
        "sample_rate": SAMPLE_RATE, "channels": CHANNELS,
        "window_samples": WINDOW, "hop_samples": HOP,
        "min_batches_per_window": MIN_BATCHES, "batch_samples": BATCH_SIZE,
        "feature_names_per_channel": FEATURE_NAMES,
    }
    if not isinstance(artifact, dict) or any(artifact.get(k) != v for k, v in expected.items()):
        raise ValueError("Model metadata does not match this version of train_wrist_rf.py.")
    model = artifact["model"]
    if set(model.classes_) != set(LABELS) or model.n_features_in_ != CHANNELS * len(FEATURE_NAMES):
        raise ValueError("Expected an eight-channel model for rest, wrist flexion, and wrist extension.")
    # Single-window inference does not benefit from dispatching a second job.
    model.n_jobs = 1
    return model, path


class RollingWindows:
    def __init__(self):
        self.batches = deque()
        self.next_end = None

    def reset(self):
        self.batches.clear()
        self.next_end = None

    def push(self, sequence, samples):
        if self.batches and sequence <= self.batches[-1][0]:
            return None
        if self.next_end is None:
            self.next_end = sequence + WINDOW_BATCHES - 1
        self.batches.append((sequence, samples))
        if sequence < self.next_end:
            return None

        # If packets went missing, advance to the newest window that has ended.
        end = self.next_end + (sequence - self.next_end) // HOP_BATCHES * HOP_BATCHES
        self.next_end = end + HOP_BATCHES
        start = end - WINDOW_BATCHES + 1
        while self.batches[0][0] < start:
            self.batches.popleft()
        observed = [(seq, block) for seq, block in self.batches if seq <= end]
        if len(observed) < MIN_BATCHES:
            return None, len(observed)

        sequences = np.asarray([seq for seq, _ in observed])
        samples = np.concatenate([block for _, block in observed], axis=0)
        return window_features(samples, sequences), len(observed)


def display(message, terminal):
    if terminal:
        print("\r" + message.ljust(96), end="", flush=True)
    else:
        print(message, flush=True)


def predict(model, command):
    terminal = sys.stdout.isatty()
    windows = RollingWindows()
    stale = False
    print("Waiting for raw EMG. Press Ctrl+C to stop.", flush=True)
    try:
        with Stream(command) as stream:
            batch = stream.next_batch(time.monotonic() + 75)
            if batch is None:
                raise RuntimeError("No decoded EMG arrived in 75 seconds. Check the band connection.")
            while True:
                if batch is None or time.monotonic() - batch.received_at > MAX_SAMPLE_AGE:
                    windows.reset()
                    if not stale:
                        display("Waiting for fresh EMG…", terminal)
                        stale = True
                else:
                    stale = False
                    try:
                        samples = np.asarray(batch.samples, dtype=np.uint16)
                    except (TypeError, ValueError, OverflowError):
                        samples = None
                    if samples is not None and samples.shape == (BATCH_SIZE, CHANNELS):
                        result = windows.push(batch.sequence, samples)
                        if result is not None:
                            features, count = result
                            if features is None:
                                display(f"Waiting for EMG: {count}/{WINDOW_BATCHES} batches in window", terminal)
                            else:
                                scores = model.predict_proba(features.reshape(1, -1))[0]
                                best = scores.argmax()
                                label = str(model.classes_[best]).replace("_", " ")
                                display(f"{label:<18}  RF score {scores[best]:.0%}  ({count}/{WINDOW_BATCHES} batches)", terminal)
                batch = stream.next_batch(time.monotonic() + MAX_SAMPLE_AGE)
    finally:
        if terminal:
            print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True, help="band's bonded Bluetooth MAC")
    parser.add_argument("--model", type=Path, required=True, help="recording folder or wrist_rf.joblib")
    parser.add_argument("--kinesis", type=Path, default=Path(".venv/bin/kinesis-linux"))
    args = parser.parse_args()
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", args.address):
        parser.error("--address must be a Bluetooth MAC such as AA:BB:CC:DD:EE:FF")
    if not args.kinesis.is_file():
        parser.error(f"Kinesis executable not found: {args.kinesis}")
    try:
        model, path = load_model(args.model.expanduser())
        print(f"Using {path}", flush=True)
        predict(model, [str(args.kinesis.resolve()), "listen", "--address", args.address, "--raw-emg"])
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
