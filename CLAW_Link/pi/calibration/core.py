"""BLE-independent calibration state machine. No sensor or actuator control."""
import asyncio
from collections import OrderedDict, defaultdict
import json
import logging
from pathlib import Path
import secrets
import struct
import time
import joblib
import numpy as np
from emg_common import records, windows, model

IDLE, PREPARING, READY, COLLECTING, CALIBRATED, TRAINING, INFERENCE, ERROR = range(8)
NAMES = ['Hand at rest', 'Fist', 'Wrist flexion', 'Wrist extension', 'Radial deviation', 'Ulnar deviation']

class Dataset:
    def __init__(self, root, output, seed=None):
        self.root, self.output = Path(root), Path(output)
        self.rng = np.random.default_rng(seed)
        self.seed = seed if seed is not None else secrets.randbelow(2**31)
        self.features = {}

    def select(self):
        data = [r for r in records('gestures', self.root) if 1 <= r[3] <= 6]
        sessions = defaultdict(lambda: defaultdict(list))
        for r in data:
            sessions[r[1]][r[2]].append(r)
        eligible = defaultdict(list)
        for session, trials in sessions.items():
            complete = [t for t, rs in trials.items() if {r[3] for r in rs} == set(range(1,7))]
            if len(complete) >= 2:
                eligible[session.split('/')[0]].append((session, trials, complete))
        if not eligible:
            raise ValueError('Need manifest.json and a participant/date with two recordings containing all six gestures.')
        subject = self.rng.choice(sorted(eligible))
        choices = eligible[subject]
        self.session, trials, complete = choices[int(self.rng.integers(len(choices)))]
        self.train_trial = str(self.rng.choice(sorted(complete)))
        self.train = trials[self.train_trial]
        self.test = [r for t in complete if t != self.train_trial for r in trials[t]]
        self.features = {}
        logging.info('Selected %s; train recording %s; test recordings %s', self.session, self.train_trial, sorted({r[2] for r in self.test}))
        return int(subject.split('_')[1])

    def collect(self, gesture):
        # Reuses the earlier loader: 250-row windows, 125-row stride,
        # Hudgins features, threshold 1e-5. CSV segment boundaries are retained.
        arrays = [windows(r, 'gestures', 250, 125, 'hudgins', 1e-5) for r in self.train if r[3] == gesture]
        if not arrays:
            raise ValueError(f'No training files for G{gesture}')
        self.features[gesture] = np.concatenate(arrays)
        logging.info('G%s prepared: %s windows', gesture, len(self.features[gesture]))

    def fit(self):
        if set(self.features) != set(range(1,7)):
            raise ValueError('All six gestures must be collected before fitting.')
        x = np.concatenate([self.features[g] for g in range(1,7)])
        y = np.concatenate([np.full(len(self.features[g]), g) for g in range(1,7)])
        estimator = model('rf', self.seed).set_params(n_jobs=2)
        estimator.fit(x, y)
        xt, yt = [], []
        for r in self.test:
            v = windows(r, 'gestures', 250, 125, 'hudgins', 1e-5)
            xt.append(v); yt.extend([r[3]] * len(v))
        pred = estimator.predict(np.concatenate(xt))
        accuracy = float(np.mean(pred == np.asarray(yt)))
        settings = dict(dataset='gestures', session=self.session, train_recording=self.train_trial,
                        test_recordings=sorted({r[2] for r in self.test}), features='hudgins',
                        window=250, step=125, threshold=1e-5, seed=self.seed, labels=NAMES,
                        test_accuracy=accuracy, train_windows=len(x), test_windows=len(yt))
        dest = self.output / self.session / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
        dest.mkdir(parents=True, exist_ok=False)
        joblib.dump({'pipeline': estimator, 'settings': settings}, dest / 'rf_model.joblib')
        (dest / 'report.json').write_text(json.dumps(settings, indent=2))
        self.estimator = estimator
        logging.info('Training finished; held-out accuracy %.1f%%; saved %s', 100*accuracy, dest)
        return accuracy

class Calibration:
    def __init__(self, dataset, seconds=3):
        self.dataset, self.seconds = dataset, seconds
        self.boot = secrets.randbits(32)
        self.phase = IDLE
        self.completed = self.current = self.progress = self.error = self.ack = self.subject = self.revision = 0
        self.accuracy = 65535
        self.task = None
        self.seen = OrderedDict()
        self.seq = 0
        self.channels = np.full(8, 500.0)

    def snapshot(self):
        return struct.pack('<6BIIHHH', 1, self.phase, self.completed, self.current, self.progress,
                           self.error, self.boot, self.ack, self.subject, self.accuracy, self.revision)

    def change(self, phase):
        self.phase = phase
        self.revision = (self.revision + 1) % 65536
        logging.info('Mode=%s completed=%s/6', ['idle','preparing','calibration mode','collecting','calibration done','training','inference mode','error'][phase], self.completed)

    def command(self, payload):
        # v1, opcode (1=start, 2=gesture), server boot ID, random request ID, gesture.
        if len(payload) != 11:
            self.error = 1; return
        version, op, boot, request, gesture = struct.unpack('<BBIIB', payload)
        if version != 1 or boot != self.boot or not request:
            self.error = 1; return
        if request in self.seen:
            if self.seen[request][0] != payload:
                self.error = 1; return
            self.ack, self.error = request, self.seen[request][1]
            return
        self.ack, self.error = request, 0
        if op == 1 and self.phase in (IDLE, INFERENCE, ERROR) and (not self.task or self.task.done()):
            self.completed = self.current = self.progress = self.subject = 0
            self.accuracy = 65535
            self.change(PREPARING)
            self.task = asyncio.create_task(self.prepare())
        elif op == 2 and self.phase == READY and gesture == self.completed + 1 and 1 <= gesture <= 6:
            self.current, self.progress = gesture, 0
            self.change(COLLECTING)
            self.task = asyncio.create_task(self.collect(gesture))
        else:
            self.error = 2  # wrong phase / gesture; no state transition
        self.seen[request] = (payload, self.error)
        if len(self.seen) > 256:
            self.seen.popitem(last=False)

    async def prepare(self):
        try:
            self.subject = await asyncio.to_thread(self.dataset.select)
            self.change(READY)
        except Exception:
            self.fail()

    def fail(self):
        logging.exception('Calibration failed')
        self.error = 3
        self.change(ERROR)

    async def collect(self, gesture):
        try:
            for step in range(1, 11):
                await asyncio.sleep(self.seconds / 10)
                self.progress = step * 9
            await asyncio.to_thread(self.dataset.collect, gesture)
            self.completed, self.progress = gesture, 100
            if gesture < 6:
                self.change(READY)
            else:
                self.change(CALIBRATED)
                await asyncio.sleep(2)
                self.change(TRAINING)
                result = await asyncio.to_thread(self.dataset.fit)
                self.accuracy = round(result * 10000)
                self.change(INFERENCE)
        except Exception:
            self.fail()

    def telemetry(self):
        if self.phase != INFERENCE:
            return struct.pack('<I8H', 0, *([0]*8))
        # OS randomness (seeded by the kernel's entropy pool), not EMG or model output.
        noise = np.frombuffer(secrets.token_bytes(16), dtype='<u2').astype(float) / 65535
        self.channels = np.clip(self.channels * .93 + 500 * .07 + (noise - .5) * 140, 0, 1000)
        self.seq = (self.seq + 1) % 2**32 or 1
        return struct.pack('<I8H', self.seq, *np.rint(self.channels).astype(int))
