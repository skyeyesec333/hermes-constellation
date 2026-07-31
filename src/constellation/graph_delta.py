"""Bounded graph delta: snapshots and diffs (i2-successor Wave 4 Task 4.3).

Snapshots hash the canonical graph (active relationships + their entity
endpoints) deterministically; diffs compare two snapshots and report
added/removed/changed edges plus an unchanged count, bounded, with a
receipt. Superseded relationships drop out of the active graph and appear
as removals. Candidates never enter a snapshot.

Receipts live under .constellation/graph-snapshots/ and
.constellation/graph-deltas/ — derived artifacts, deterministic names.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .predicates import default_registry
from .storage import atomic_write_text
from .vault import is_initialized

_SNAPSHOT_DIR = Path(".constellation/graph-snapshots")
_DELTA_DIR = Path(".constellation/graph-deltas")
_DIFF_LIST_LIMIT = 200


class GraphDeltaError(RuntimeError):
    """Raised when snapshot or diff operations fail closed."""


def _edge_id(subject: str, predicate: str, obj: str, record_id: str) -> str:
    blob = f"relationship|{record_id}|{subject}|{predicate}|{obj}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _canonical_graph(vault: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry = default_registry()
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    base = vault / "relationships"
    if base.is_dir():
        for path in sorted(base.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not metadata.get("id"):
                continue
            if str(metadata.get("status", "")) in {"stale", "superseded"}:
                continue
            subject = str(metadata.get("subject_id", ""))
            obj = str(metadata.get("object_id", ""))
            predicate = str(metadata.get("predicate", ""))
            resolution = registry.resolve(predicate) if registry else None
            canonical = (
                str(resolution.canonical)
                if resolution is not None and resolution.status != "unknown"
                else predicate
            )
            node_ids.update((subject, obj))
            raw_sources = metadata.get("source_ids") or []
            edges.append(
                {
                    "edge_id": _edge_id(subject, canonical, obj, str(metadata["id"])),
                    "record_id": str(metadata["id"]),
                    "subject_id": subject,
                    "predicate": canonical,
                    "object_id": obj,
                    "source_ids": sorted(
                        str(s) for s in (raw_sources if isinstance(raw_sources, list) else [])
                    ),
                    "valid_from": str(metadata["valid_from"]) if metadata.get("valid_from") else None,
                    "valid_to": str(metadata["valid_to"]) if metadata.get("valid_to") else None,
                }
            )
    edges.sort(key=lambda edge: edge["edge_id"])
    nodes = [{"node_id": node_id} for node_id in sorted(node_ids)]
    return nodes, edges


def snapshot_graph(root: Path | str) -> dict[str, Any]:
    """Write a deterministic snapshot of the canonical graph. Same state →
    same file (overwritten, never accumulated)."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise GraphDeltaError("vault is not initialized")
    nodes, edges = _canonical_graph(vault)
    snapshot_hash = hashlib.sha256(
        json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    payload = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot_hash": snapshot_hash,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }
    relative = _SNAPSHOT_DIR / f"snapshot-{snapshot_hash[:12]}.json"
    atomic_write_text(vault, relative, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return {
        "status": "ok",
        "snapshot_hash": snapshot_hash,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "snapshot_path": relative.as_posix(),
    }


def _load_snapshot(vault: Path, relative: str) -> dict[str, Any]:
    path = vault / relative
    if path.is_symlink() or not path.is_file():
        raise GraphDeltaError(f"snapshot not found: {relative}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraphDeltaError(f"snapshot is invalid: {relative}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise GraphDeltaError(f"snapshot has unsupported version: {relative}")
    if not isinstance(payload.get("edges"), list):
        raise GraphDeltaError(f"snapshot is invalid: {relative}")
    return payload


def diff_snapshots(
    root: Path | str, from_snapshot: str, to_snapshot: str
) -> dict[str, Any]:
    """Diff two snapshots: added/removed/changed edges + unchanged count.

    Bounded per-section lists with truncated flags; writes a deterministic
    receipt under .constellation/graph-deltas/.
    """
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise GraphDeltaError("vault is not initialized")
    before = _load_snapshot(vault, from_snapshot)
    after = _load_snapshot(vault, to_snapshot)
    before_edges = {edge["edge_id"]: edge for edge in before["edges"]}
    after_edges = {edge["edge_id"]: edge for edge in after["edges"]}

    added = [after_edges[key] for key in sorted(after_edges.keys() - before_edges.keys())]
    removed = [before_edges[key] for key in sorted(before_edges.keys() - after_edges.keys())]
    changed = [
        {"edge_id": key, "before": before_edges[key], "after": after_edges[key]}
        for key in sorted(before_edges.keys() & after_edges.keys())
        if before_edges[key] != after_edges[key]
    ]
    unchanged = sum(
        1
        for key in before_edges.keys() & after_edges.keys()
        if before_edges[key] == after_edges[key]
    )

    def _bound(items: list) -> tuple[list, bool]:
        return items[:_DIFF_LIST_LIMIT], len(items) > _DIFF_LIST_LIMIT

    added_bounded, added_truncated = _bound(added)
    removed_bounded, removed_truncated = _bound(removed)
    changed_bounded, changed_truncated = _bound(changed)

    receipt = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "from_snapshot": from_snapshot,
        "to_snapshot": to_snapshot,
        "from_hash": before.get("snapshot_hash"),
        "to_hash": after.get("snapshot_hash"),
        "added": added_bounded,
        "removed": removed_bounded,
        "changed": changed_bounded,
        "unchanged": unchanged,
        "totals": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": unchanged,
        },
        "truncated": {
            "added": added_truncated,
            "removed": removed_truncated,
            "changed": changed_truncated,
        },
    }
    receipt_rel = (
        _DELTA_DIR
        / f"diff-{str(before.get('snapshot_hash'))[:12]}-{str(after.get('snapshot_hash'))[:12]}.json"
    )
    atomic_write_text(vault, receipt_rel, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return {
        "status": "ok",
        "from_snapshot": from_snapshot,
        "to_snapshot": to_snapshot,
        "added": added_bounded,
        "removed": removed_bounded,
        "changed": changed_bounded,
        "unchanged": unchanged,
        "totals": receipt["totals"],
        "truncated": receipt["truncated"],
        "receipt_path": receipt_rel.as_posix(),
    }
