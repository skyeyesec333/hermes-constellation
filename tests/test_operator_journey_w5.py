"""Wave 5: operator journey regression — the canonical private journey must pass."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_operator_journey_end_to_end(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "operator_journey.py"), "--workdir", str(tmp_path / "j")],
        capture_output=True, text=True, cwd=REPO,
        env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin"},
        timeout=300,
    )
    assert proc.returncode == 0, f"journey failed:\n{proc.stdout}\n{proc.stderr}"
    assert "JOURNEY PASSED" in proc.stdout
