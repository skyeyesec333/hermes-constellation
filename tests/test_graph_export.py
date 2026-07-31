"""Tests for bounded graph export with evidence manifest (Wave 6 Task 6.1)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.graph_export import GraphExportError, export_graph
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


def _entity(vault: Path, title: str, sensitivity=Sensitivity.INTERNAL) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title, status="active",
        sensitivity=sensitivity, source_ids=[], created_at=NOW, updated_at=NOW,
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


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_vault(root)
    a = _entity(root, "Alpha")
    b = _entity(root, "Beta")
    hidden = _entity(root, "Hidden", sensitivity=Sensitivity.CONFIDENTIAL)
    s = _source(root)
    _write(root, "relationships", RelationshipRecord(
        id=generate_ulid(), title="owns", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=a, object_id=b,
        predicate="owns", source_ids=[s], evidence_class="corroborated",
        confidence=0.9, created_at=NOW, updated_at=NOW,
    ))
    _write(root, "relationships", RelationshipRecord(
        id=generate_ulid(), title="hidden link", status="active",
        sensitivity=Sensitivity.CONFIDENTIAL, subject_id=a, object_id=hidden,
        predicate="advises", source_ids=[s], evidence_class="corroborated",
        created_at=NOW, updated_at=NOW,
    ))
    return root


def test_export_json_shape_and_manifest(vault: Path, tmp_path: Path) -> None:
    out = tmp_path / "graph.json"
    result = export_graph(vault, out, format="json")

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["manifest_hash"] == result["manifest_hash"]
    assert len(payload["nodes"]) == 2  # Hidden excluded at internal ceiling
    assert len(payload["edges"]) == 1
    edge = payload["edges"][0]
    assert edge["predicate"] == "owns"
    assert edge["confidence"] == 0.9
    assert edge["edge_id"]
    assert edge["source_ids"]
    assert result["excluded_by_sensitivity"] >= 1
    # Receipt sidecar with excluded counts.
    receipt = json.loads((tmp_path / "graph.receipt.json").read_text(encoding="utf-8"))
    assert receipt["manifest_hash"] == result["manifest_hash"]
    assert receipt["excluded_by_sensitivity"] >= 1
    # No note paths or body text in the export.
    text = out.read_text(encoding="utf-8")
    assert "record_path" not in text
    assert "note" not in text.lower() or "note_path" not in text


def test_export_ndjson_lines(vault: Path, tmp_path: Path) -> None:
    out = tmp_path / "graph.ndjson"
    result = export_graph(vault, out, format="ndjson")

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    kinds = [line["kind"] for line in lines]
    assert kinds.count("node") == 2
    assert kinds.count("edge") == 1
    assert kinds[-1] == "manifest"
    assert lines[-1]["manifest_hash"] == result["manifest_hash"]


def test_export_deterministic_and_no_candidates(vault: Path, tmp_path: Path) -> None:
    candidates = vault / ".constellation" / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    (candidates / "relationship-x.json").write_text(
        json.dumps({"kind": "relationship_candidate", "record": {"id": generate_ulid()}}),
        encoding="utf-8",
    )
    out1, out2 = tmp_path / "a.json", tmp_path / "b.json"
    r1 = export_graph(vault, out1, format="json")
    r2 = export_graph(vault, out2, format="json")
    assert r1["manifest_hash"] == r2["manifest_hash"]
    payload = json.loads(out1.read_text(encoding="utf-8"))
    assert all(not e.get("candidate") for e in payload["edges"])


def test_export_validates_args(vault: Path, tmp_path: Path) -> None:
    with pytest.raises(GraphExportError):
        export_graph(vault, tmp_path / "x.json", format="graphml")
    with pytest.raises(GraphExportError):
        export_graph(vault, tmp_path / "x.json", format="json", sensitivity="cosmic")
