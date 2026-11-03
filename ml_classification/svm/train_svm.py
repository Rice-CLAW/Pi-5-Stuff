#!/usr/bin/env python3
"""Automatically use the shared environment, then run EMG training."""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
ENV = Path(os.environ.get("CLAW_PYTHON", str(ROOT / "svm/.venv/bin/python")))
if not ENV.is_file():
    raise SystemExit(f"Shared environment missing: {ENV}. Create it as described in README.md.")
# Do not resolve the interpreter symlink: sys.prefix identifies the venv.
if Path(sys.prefix).absolute() != ENV.parent.parent.absolute():
    os.execv(str(ENV), [str(ENV), str(Path(__file__).resolve()), *sys.argv[1:]])
sys.path.insert(0, str(ROOT))
from emg_common import run
if __name__ == "__main__":
    run("svm")
