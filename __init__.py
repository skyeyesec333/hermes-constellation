"""Filesystem-plugin entry point for Hermes Agent.

The Git plugin installer loads this file directly from the repository root. The
reusable Python packages remain under ``src/`` for standard packaging.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from hermes_constellation_plugin import register  # noqa: E402,F401

__all__ = ["register"]
