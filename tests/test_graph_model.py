"""Tests for the deterministic graph analysis model (Wave 3 Task 3.1)."""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.graph_model import GraphModelError, build_graph_model
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


def _entity(vault: Path, title: str, sensitivity=Sensitivity.INTERNAL) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title, status="active",
        sensitivity=sensitivity, source_ids=[], created_at=NOW, updated_at=NOW,
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
                  predicate: str = "partners_with", **kwargs) -> str:
    sensitivity = kwargs.pop("sensitivity", Sensitivity.INTERNAL)
    record = RelationshipRecord(
        id=generate_ulid(), title=predicate, status="active",
        sensitivity=sensitivity, subject_id=subject, object_id=obj,
        predicate=predicate, source_ids=[source], evidence_class="user-asserted",
        created_at=NOW, updated_at=NOW, **kwargs,
    )
    _write(vault, "relationships", record)
    return record.id


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_vault(root)
    return root


def test_missing_networkx_is_actionable(vault: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "networkx", None)
    with pytest.raises(GraphModelError, match="networkx"):
        build_graph_model(vault)


def test_model_collapses_parallel_edges_with_evidence(vault: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    c = _entity(vault, "Gamma")
    s = _source(vault)
    r1 = _relationship(vault, a, b, s)
    r2 = _relationship(vault, a, b, s, predicate="supplies")
    _relationship(vault, b, c, s)

    model = build_graph_model(vault)

    assert set(model.simple.nodes) == {a, b, c}
    edge = model.simple.get_edge_data(a, b)
    assert edge["evidence_count"] == 2
    assert sorted(edge["record_ids"]) == sorted([r1, r2])
    assert edge["source_ids"] == [s]
    assert sorted(edge["predicates"]) == ["partners_with", "supplies"]
    # Multi graph retains per-record edges for evidence inspection.
    assert model.multi.number_of_edges() == 3
    assert model.projection_hash


def test_defaults_exclude_claims_candidates_and_higher_sensitivity(vault: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    hidden = _entity(vault, "Hidden", sensitivity=Sensitivity.CONFIDENTIAL)
    s = _source(vault)
    _relationship(vault, a, b, s)
    _relationship(vault, a, hidden, s, sensitivity=Sensitivity.CONFIDENTIAL)
    _write(vault, "claims", Claim(
        id=generate_ulid(), title="claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=b, object_id=a,
        predicate="competes_with", source_ids=[s], created_at=NOW, updated_at=NOW,
    ))

    model = build_graph_model(vault)
    assert set(model.simple.nodes) == {a, b}
    assert model.simple.number_of_edges() == 1

    with_claims = build_graph_model(vault, include_claims=True)
    assert with_claims.simple.number_of_edges() == 2


def test_model_is_deterministic(vault: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    s = _source(vault)
    _relationship(vault, a, b, s)
    _relationship(vault, b, a, s, predicate="advises")

    first = build_graph_model(vault)
    second = build_graph_model(vault)
    assert first.projection_hash == second.projection_hash
    assert sorted(first.simple.edges) == sorted(second.simple.edges)


def test_predicates_and_as_of_filters(vault: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    c = _entity(vault, "Gamma")
    s = _source(vault)
    _relationship(vault, a, b, s, predicate="owns")
    _relationship(vault, b, c, s, predicate="advises",
                  valid_to=datetime(2020, 1, 1, tzinfo=timezone.utc))

    only_owns = build_graph_model(vault, predicates={"owns"})
    assert set(only_owns.simple.edges) == {(a, b)}
    as_of = build_graph_model(vault, as_of=NOW)
    assert set(as_of.simple.edges) == {(a, b)}
