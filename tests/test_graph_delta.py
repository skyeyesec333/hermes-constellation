"""Tests for bounded graph delta snapshots and diffs (Wave 4 Task 4.3)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.graph_delta import GraphDeltaError, diff_snapshots, snapshot_graph
from constellation.models import (
    EntityKind,
    EntityRecord,
    RelationshipRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _write(vault: Path, folder: str, record, body: str = "# note\n") -> None:
    target = vault / folder / f"{record.id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), body),
        encoding="utf-8",
    )


def _entity(vault: Path, title: str) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title, status="active",
        sensitivity=Sensitivity.INTERNAL, source_ids=[], created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", record)
    return record.id


def _source(vault: Path) -> str:
    record = SourceItem(
        id=generate_ulid(), type="source_item", title="Filing",
        status="active", sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"x").hexdigest(),
        original_path="Library/Files/f.pdf", media_type="application/pdf",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "source-items", record)
    return record.id


def _rel(vault: Path, subject: str, obj: str, source: str, predicate: str = "owns") -> str:
    record = RelationshipRecord(
        id=generate_ulid(), title=predicate, status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=subject, object_id=obj,
        predicate=predicate, source_ids=[source], evidence_class="corroborated",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "relationships", record)
    return record.id


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_vault(root)
    return root


def test_snapshot_is_deterministic_and_bounded(vault: Path) -> None:
    a, b = _entity(vault, "A"), _entity(vault, "B")
    s = _source(vault)
    _rel(vault, a, b, s)

    first = snapshot_graph(vault)
    second = snapshot_graph(vault)

    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert first["edge_count"] == 1
    assert (vault / first["snapshot_path"]).is_file()
    # Same graph state overwrites the same snapshot file, never accumulates.
    assert first["snapshot_path"] == second["snapshot_path"]


def test_diff_reports_added_removed_changed_unchanged(vault: Path) -> None:
    a, b, c = (_entity(vault, t) for t in ("A", "B", "C"))
    s = _source(vault)
    owns_id = _rel(vault, a, b, s)
    snap1 = snapshot_graph(vault)

    _rel(vault, b, c, s, "advises")  # added
    # Supersede: mark the owns relationship superseded (removed from active graph)
    rel_path = vault / "relationships" / f"{owns_id}.md"
    text = rel_path.read_text(encoding="utf-8")
    rel_path.write_text(text.replace("status: active", "status: superseded"), encoding="utf-8")
    snap2 = snapshot_graph(vault)

    diff = diff_snapshots(vault, snap1["snapshot_path"], snap2["snapshot_path"])

    assert diff["status"] == "ok"
    assert len(diff["added"]) == 1
    assert diff["added"][0]["predicate"] == "advises"
    assert len(diff["removed"]) == 1
    assert diff["removed"][0]["predicate"] == "owns"
    assert diff["unchanged"] == 0
    assert (vault / diff["receipt_path"]).is_file()


def test_diff_unchanged_graph_is_explicit(vault: Path) -> None:
    a, b = _entity(vault, "A"), _entity(vault, "B")
    s = _source(vault)
    _rel(vault, a, b, s)
    snap = snapshot_graph(vault)

    diff = diff_snapshots(vault, snap["snapshot_path"], snap["snapshot_path"])

    assert diff["added"] == []
    assert diff["removed"] == []
    assert diff["changed"] == []
    assert diff["unchanged"] == 1


def test_diff_validates_inputs(vault: Path) -> None:
    a, b = _entity(vault, "A"), _entity(vault, "B")
    s = _source(vault)
    _rel(vault, a, b, s)
    snap = snapshot_graph(vault)
    with pytest.raises(GraphDeltaError):
        diff_snapshots(vault, snap["snapshot_path"], ".constellation/graph-snapshots/nope.json")


def test_snapshot_excludes_candidates(vault: Path) -> None:
    a, b = _entity(vault, "A"), _entity(vault, "B")
    s = _source(vault)
    _rel(vault, a, b, s)
    packet = {"kind": "relationship_candidate", "record": {"id": generate_ulid()}}
    candidates = vault / ".constellation" / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    (candidates / "relationship-x.json").write_text(json.dumps(packet), encoding="utf-8")

    snap = snapshot_graph(vault)
    assert snap["edge_count"] == 1
