#!/usr/bin/env python3
"""Show live wrist predictions and optionally drive one bench motor."""
import argparse
from collections import deque
from contextlib import contextmanager, nullcontext
from pathlib import Path
import re
import signal
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
MOTOR_DIRECTION = {"wrist_flexion": "forward", "wrist_extension": "backward"}
MOTOR_DUTY_CYCLE = 90 / 255
REVERSAL_DELAY = 0.2


class WristMotor:
    def __init__(self, standby_pin, ain1, ain2, pwm):
        self.standby_pin = standby_pin
        self.ain1 = ain1
        self.ain2 = ain2
        self.pwm = pwm
        self.active = None
        self.last_direction = None
        self.stopped_at = 0
        self.candidate = None
        self.deadline = 0

    @property
    def state(self):
        return self.active or "standby"

    def standby(self):
        self.standby_pin.off()
        self.pwm.value = 0
        if self.active is not None:
            self.stopped_at = time.monotonic()
            self.active = None
        self.candidate = None
        self.deadline = 0

    def check_freshness(self):
        if self.deadline and time.monotonic() >= self.deadline:
            self.standby()

    def update(self, label, received_at):
        direction = MOTOR_DIRECTION.get(label)
        deadline = received_at + MAX_SAMPLE_AGE
        if direction is None or not time.monotonic() < deadline:
            self.standby()
            return

        if self.active is not None and direction != self.active:
            self.standby()
        self.deadline = deadline
        if direction == self.active:
            return

        # Require two consecutive predictions before starting motion.
        if self.candidate != direction:
            self.candidate = direction
            return
        if (self.last_direction is not None and direction != self.last_direction
                and time.monotonic() - self.stopped_at < REVERSAL_DELAY):
            return

        self.ain1.value = direction == "forward"
        self.ain2.value = direction == "backward"
        self.pwm.value = MOTOR_DUTY_CYCLE
        if time.monotonic() >= deadline:
            self.standby()
            return
        self.standby_pin.on()
        self.active = self.last_direction = direction


@contextmanager
def open_motor():
    try:
        from gpiozero import DigitalOutputDevice, PWMOutputDevice
    except ImportError:
        raise RuntimeError("Motor mode needs python3-gpiozero and python3-lgpio. See README.") from None

    with (
        DigitalOutputDevice(25, initial_value=False) as standby_pin,
        DigitalOutputDevice(23, initial_value=False) as ain1,
        DigitalOutputDevice(24, initial_value=False) as ain2,
        PWMOutputDevice(18, initial_value=0, frequency=500) as pwm,
    ):
        motor = WristMotor(standby_pin, ain1, ain2, pwm)
        try:
            yield motor
        finally:
            motor.standby()


def stop_on_sigterm(_signum, _frame):
    raise KeyboardInterrupt


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


def predict(model, command, drive_motor=False):
    terminal = sys.stdout.isatty()
    windows = RollingWindows()
    stale = False
    print("Waiting for raw EMG. Press Ctrl+C to stop.", flush=True)
    try:
        motor_context = open_motor() if drive_motor else nullcontext()
        with Stream(command) as stream, motor_context as motor:
            if motor is not None:
                print("Motor enabled for this session. Initial state: standby.", flush=True)
            batch = stream.next_batch(time.monotonic() + 75)
            if batch is None:
                raise RuntimeError("No decoded EMG arrived in 75 seconds. Check the band connection.")
            while True:
                if motor is not None:
                    motor.check_freshness()
                if batch is None or time.monotonic() - batch.received_at > MAX_SAMPLE_AGE:
                    windows.reset()
                    if motor is not None:
                        motor.standby()
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
                                motor_status = ""
                                if motor is not None:
                                    motor.candidate = None
                                    motor_status = f"  Motor: {motor.state}"
                                display(f"Waiting for EMG: {count}/{WINDOW_BATCHES} batches in window"
                                        f"{motor_status}", terminal)
                            else:
                                scores = model.predict_proba(features.reshape(1, -1))[0]
                                best = scores.argmax()
                                label = str(model.classes_[best])
                                motor_status = ""
                                if motor is not None:
                                    motor.update(label, batch.received_at)
                                    motor_status = f"  Motor: {motor.state}"
                                display(f"{label.replace('_', ' '):<18}  RF score {scores[best]:.0%}  "
                                        f"({count}/{WINDOW_BATCHES} batches){motor_status}", terminal)
                    elif motor is not None:
                        windows.reset()
                        motor.standby()
                deadline = time.monotonic() + MAX_SAMPLE_AGE
                if motor is not None and motor.deadline:
                    deadline = min(deadline, motor.deadline)
                batch = stream.next_batch(deadline)
    finally:
        if terminal:
            print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True, help="band's bonded Bluetooth MAC")
    parser.add_argument("--model", type=Path, required=True, help="recording folder or wrist_rf.joblib")
    parser.add_argument("--kinesis", type=Path, default=Path(".venv/bin/kinesis-linux"))
    parser.add_argument("--motor", action="store_true", help="drive TB6612FNG channel A on the bench")
    args = parser.parse_args()
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", args.address):
        parser.error("--address must be a Bluetooth MAC such as AA:BB:CC:DD:EE:FF")
    if not args.kinesis.is_file():
        parser.error(f"Kinesis executable not found: {args.kinesis}")
    previous_sigterm = signal.signal(signal.SIGTERM, stop_on_sigterm) if args.motor else None
    try:
        model, path = load_model(args.model.expanduser())
        print(f"Using {path}", flush=True)
        predict(model, [str(args.kinesis.resolve()), "listen", "--address", args.address, "--raw-emg"],
                drive_motor=args.motor)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    main()
