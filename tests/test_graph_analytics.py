"""Tests for SNA metrics and analysis receipts (Wave 3 Task 3.2)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.graph_analytics import GraphAnalyticsError, compute_graph_analytics
from constellation.models import (
    Claim,
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
        id=generate_ulid(), type="source_item", title="Brief",
        status="active", sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"x").hexdigest(),
        original_path="Library/Files/b.pdf", media_type="application/pdf",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "source-items", record)
    return record.id


def _relationship(vault: Path, subject: str, obj: str, source: str,
                  predicate: str = "partners_with") -> str:
    record = RelationshipRecord(
        id=generate_ulid(), title=predicate, status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=subject, object_id=obj,
        predicate=predicate, source_ids=[source], evidence_class="user-asserted",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "relationships", record)
    return record.id


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_vault(root)
    a = _entity(root, "Alpha")
    b = _entity(root, "Beta")
    c = _entity(root, "Gamma")
    d = _entity(root, "Delta")
    s = _source(root)
    _relationship(root, a, b, s)
    _relationship(root, b, c, s)
    _relationship(root, c, a, s)   # triangle
    _relationship(root, d, a, s, predicate="funds")
    return root


def test_metrics_shape_and_receipt(vault: Path) -> None:
    result = compute_graph_analytics(vault)

    assert result["status"] == "ok"
    assert result["node_count"] == 4
    assert result["approximation"] is False
    assert result["components"]["count"] == 1
    assert result["components"]["largest"] == 4
    assert len(result["top_nodes"]) == 4
    for node in result["top_nodes"]:
        for metric in ("degree", "in_degree", "out_degree", "closeness",
                       "betweenness", "pagerank"):
            assert metric in node
        assert node["node_id"]
    assert result["projection_hash"]
    receipt = vault / result["receipt_path"]
    assert receipt.is_file()
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["family"] == "sna-report"
    assert payload["projection_hash"] == result["projection_hash"]
    assert payload["parameters"]["sensitivity_ceiling"] == "internal"


def test_deterministic_metrics(vault: Path) -> None:
    first = compute_graph_analytics(vault)
    second = compute_graph_analytics(vault)
    assert first["top_nodes"] == second["top_nodes"]
    assert first["projection_hash"] == second["projection_hash"]
    # Deterministic receipt name — same analysis overwrites, never accumulates.
    assert first["receipt_path"] == second["receipt_path"]


def test_top_bound_and_truncation_flag(vault: Path) -> None:
    result = compute_graph_analytics(vault, top=2)
    assert len(result["top_nodes"]) == 2
    assert result["truncated"] is True
    full = compute_graph_analytics(vault, top=25)
    assert full["truncated"] is False


def test_ranking_is_explainable(vault: Path) -> None:
    result = compute_graph_analytics(vault)
    # Triangle + Delta→Alpha: Alpha has the highest in-degree and pagerank.
    top = result["top_nodes"][0]
    titles = {node["node_id"]: node["title"] for node in result["top_nodes"]}
    assert titles[top["node_id"]] == "Alpha"
    assert top["in_degree"] >= 2


def test_empty_graph_is_explicit(vault: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    initialize_vault(empty)
    result = compute_graph_analytics(empty)
    assert result["status"] == "ok"
    assert result["node_count"] == 0
    assert result["top_nodes"] == []


def test_missing_networkx_is_actionable(vault: Path, monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "networkx", None)
    with pytest.raises(GraphAnalyticsError, match="networkx"):
        compute_graph_analytics(vault)


def test_include_claims_and_as_of_parameters_recorded(vault: Path) -> None:
    s = next((vault / "source-items").glob("*.md")).stem
    a, b, c = (n.stem for n in sorted((vault / "entities").glob("*.md"))[:3])
    _write(vault, "claims", Claim(
        id=generate_ulid(), title="claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=a, object_id=b,
        predicate="competes_with", source_ids=[s], created_at=NOW, updated_at=NOW,
    ))
    result = compute_graph_analytics(vault, include_claims=True, as_of=NOW)
    assert result["parameters"]["include_claims"] is True
    assert result["parameters"]["as_of"] is not None
    receipt = json.loads((vault / result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["parameters"]["include_claims"] is True
