"""Rebuildable segment-level FTS for long-form sources."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .storage import atomic_write_text, safe_relative_path
from .vault import is_initialized


class SegmentIndexError(RuntimeError):
    """Raised when segment index operations fail closed."""


def _index_paths(root: Path, source_id: str) -> tuple[Path, Path]:
    relative_dir = Path(".constellation/derived") / source_id
    db_relative = relative_dir / "segments.sqlite3"
    map_relative = relative_dir / "document-map.json"
    return safe_relative_path(root, db_relative), safe_relative_path(root, map_relative)


def build_segment_index(
    root: Path | str,
    *,
    source_id: str,
    segments: list[dict[str, Any]],
    document_map: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Create or rebuild a local segment FTS index for one source."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise SegmentIndexError("vault is not initialized")
    if not source_id:
        raise SegmentIndexError("source_id is required")
    db_path, map_path = _index_paths(vault, source_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if document_map is not None:
        atomic_write_text(
            vault,
            map_path.relative_to(vault),
            json.dumps(document_map, indent=2, sort_keys=True) + "\n",
        )

    status = "rebuilt" if db_path.exists() else "built"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".segments-", suffix=".sqlite3", dir=db_path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        connection.execute(
            "CREATE TABLE segments ("
            "segment_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, anchor TEXT NOT NULL, "
            "title TEXT, body TEXT NOT NULL, text_sha256 TEXT NOT NULL, estimated_tokens INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE VIRTUAL TABLE segment_search USING fts5("
            "segment_id UNINDEXED, source_id UNINDEXED, title, body)"
        )
        for segment in segments:
            connection.execute(
                "INSERT INTO segments(segment_id, source_id, anchor, title, body, text_sha256, estimated_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    segment["segment_id"],
                    source_id,
                    segment["anchor"],
                    segment.get("title"),
                    segment["text"],
                    segment["text_sha256"],
                    int(segment["estimated_tokens"]),
                ),
            )
            connection.execute(
                "INSERT INTO segment_search(segment_id, source_id, title, body) VALUES (?, ?, ?, ?)",
                (
                    segment["segment_id"],
                    source_id,
                    segment.get("title") or "",
                    segment["text"],
                ),
            )
        connection.commit()
        connection.close()
        temporary.replace(db_path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)

    return {
        "status": status,
        "source_id": source_id,
        "path": db_path.relative_to(vault).as_posix(),
        "segment_count": str(len(segments)),
    }


def search_segments(
    root: Path | str,
    *,
    source_id: str,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search one source's segment index and return anchored hits."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise SegmentIndexError("vault is not initialized")
    db_path, _ = _index_paths(vault, source_id)
    if not db_path.is_file():
        raise SegmentIndexError("segment index does not exist")
    if limit < 1:
        raise SegmentIndexError("limit must be positive")

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT s.segment_id, s.source_id, s.anchor, s.title, snippet(segment_search, 3, '', '', ' … ', 12) "
            "FROM segment_search "
            "JOIN segments s ON s.segment_id = segment_search.segment_id "
            "WHERE segment_search MATCH ? "
            "LIMIT ?",
            (query, limit),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "segment_id": row[0],
            "source_id": row[1],
            "anchor": row[2],
            "title": row[3],
            "snippet": row[4],
        }
        for row in rows
    ]
