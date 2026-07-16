"""Bounded, machine-readable local capability diagnostics."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from .models import SCHEMA_VERSION
from .operator import operator_context_status
from .vault import is_initialized


def _fts5_available() -> bool:
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(value)")
        connection.close()
        return True
    except sqlite3.Error:
        return False


_HYGIENE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("apple_double", "._*"),
    ("ds_store", "**/.DS_Store"),
)


def _vault_hygiene(root: Path) -> dict[str, object]:
    """Report on common filesystem cruft (AppleDouble, DS_Store, etc)."""
    cruft_count = 0
    details: dict[str, dict[str, object]] = {}
    for key, pattern in _HYGIENE_PATTERNS:
        matches = list(root.glob(pattern))
        if matches:
            details[key] = {
                "count": len(matches),
                "pattern": pattern,
                "sample_paths": [str(m.relative_to(root)) for m in matches[:5]],
            }
            cruft_count += len(matches)
    return {
        "clean": cruft_count == 0,
        "cruft_count": cruft_count,
        "items": details,
    }


def doctor_report(root: Path | str) -> dict[str, object]:
    target = Path(root).absolute()
    initialized = is_initialized(target)
    rapidocr = importlib.util.find_spec("rapidocr_onnxruntime") is not None
    pymupdf = importlib.util.find_spec("fitz") is not None
    pillow = importlib.util.find_spec("PIL") is not None
    return {
        "schema_version": SCHEMA_VERSION,
        "vault": {
            "initialized": initialized,
            "root_exists": target.exists(),
            "root_is_symlink": target.is_symlink(),
            "writable": initialized and target.is_dir(),
        },
        "operator_context": operator_context_status(target) if initialized else {"status": "absent"},
        "capabilities": {
            "sqlite_fts5": _fts5_available(),
            "text_ingest": True,
            "pdf_pymupdf": pymupdf,
            "pdf_scanned_rapidocr": pymupdf and rapidocr,
            "docx_python_docx": importlib.util.find_spec("docx") is not None,
            "pptx_python_pptx": importlib.util.find_spec("pptx") is not None,
            "pptx_markitdown": importlib.util.find_spec("markitdown") is not None,
            "xlsx_openpyxl": importlib.util.find_spec("openpyxl") is not None,
            "image_rapidocr": pillow and rapidocr,
            "mime_libmagic": importlib.util.find_spec("magic") is not None,
            "ooxml_archive_safety": True,
        },
        "vault_hygiene": _vault_hygiene(target) if initialized else {"clean": False, "cruft_count": 0, "items": {}},
    }


def doctor_json(root: Path | str) -> str:
    return json.dumps(doctor_report(root), sort_keys=True, separators=(",", ":"))
