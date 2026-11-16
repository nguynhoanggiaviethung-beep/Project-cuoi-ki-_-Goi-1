"""Append UTF-8 worker output to the project log for Task Scheduler."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
WORKER = ROOT / "scripts" / "ssi_worker.py"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "ssi_worker.log"

if not PYTHON.exists():
    raise SystemExit(f"Không tìm thấy Python của môi trường dự án: {PYTHON}")
LOG_DIR.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
env["PYTHONUTF8"] = "1"
with LOG_FILE.open("ab", buffering=0) as output:
    result = subprocess.run(
        [str(PYTHON), "-u", str(WORKER)],
        cwd=ROOT,
        env=env,
        stdout=output,
        stderr=subprocess.STDOUT,
        check=False,
    )
raise SystemExit(result.returncode)
