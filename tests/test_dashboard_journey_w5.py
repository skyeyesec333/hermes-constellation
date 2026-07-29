"""W5.3 regression: dashboard journey probe passes on a loopback instance.

Skips when the dashboard host deps (fastapi/uvicorn/httpx) are not installed
in the current environment — the probe runs under the dashboard host python,
not necessarily the repo venv.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _host_python() -> str | None:
    for candidate in (sys.executable, shutil.which("python3")):
        if not candidate:
            continue
        probe = subprocess.run(
            [candidate, "-c", "import fastapi, uvicorn, httpx"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return candidate
    return None


def test_dashboard_journey_end_to_end() -> None:
    python = _host_python()
    if python is None:
        pytest.skip("fastapi/uvicorn/httpx not available in this environment")
    proc = subprocess.run(
        [python, str(REPO / "scripts" / "dashboard_journey.py")],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=180,
    )
    if proc.returncode == 2:
        pytest.skip(f"dashboard plugin not installed: {proc.stdout.strip()}")
    assert proc.returncode == 0, f"dashboard journey failed:\n{proc.stdout}\n{proc.stderr}"
