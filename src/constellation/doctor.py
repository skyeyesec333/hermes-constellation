"""Bounded, machine-readable local capability diagnostics."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from .models import SCHEMA_VERSION
from .vault import is_initialized


def _fts5_available() -> bool:
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(value)")
        connection.close()
        return True
    except sqlite3.Error:
        return False


def doctor_report(root: Path | str) -> dict[str, object]:
    target = Path(root).absolute()
    initialized = is_initialized(target)
    return {
        "schema_version": SCHEMA_VERSION,
        "vault": {
            "initialized": initialized,
            "root_exists": target.exists(),
            "root_is_symlink": target.is_symlink(),
            "writable": initialized and target.is_dir(),
        },
        "capabilities": {
            "sqlite_fts5": _fts5_available(),
            "pdf_pymupdf": importlib.util.find_spec("fitz") is not None,
            "text_ingest": True,
        },
    }


def doctor_json(root: Path | str) -> str:
    return json.dumps(doctor_report(root), sort_keys=True, separators=(",", ":"))
