"""Tests for deterministic typology detection (Wave 4 Task 4.1)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.models import (
    EntityKind,
    EntityRecord,
    RelationshipRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.typologies import scan_typologies
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


def test_layered_ownership_chain(vault: Path) -> None:
    a, b, c = (_entity(vault, t) for t in ("A", "B", "C"))
    s = _source(vault)
    r1 = _rel(vault, a, b, s)
    r2 = _rel(vault, b, c, s)

    result = scan_typologies(vault)

    layered = [m for m in result["matches"] if m["typology"] == "layered_ownership"]
    assert len(layered) == 1
    match = layered[0]
    assert set(match["member_ids"]) == {a, b, c}
    assert sorted(match["edge_ids"]) == sorted([r1, r2])
    assert match["source_ids"] == [s]
    assert match["summary"]


def test_circular_ownership_cycle(vault: Path) -> None:
    a, b, c = (_entity(vault, t) for t in ("A", "B", "C"))
    s = _source(vault)
    _rel(vault, a, b, s)
    _rel(vault, b, c, s)
    _rel(vault, c, a, s)

    result = scan_typologies(vault)

    circular = [m for m in result["matches"] if m["typology"] == "circular_ownership"]
    assert len(circular) == 1
    assert set(circular[0]["member_ids"]) == {a, b, c}
    assert len(circular[0]["edge_ids"]) == 3


def test_acyclic_chain_is_not_circular(vault: Path) -> None:
    a, b, c = (_entity(vault, t) for t in ("A", "B", "C"))
    s = _source(vault)
    _rel(vault, a, b, s)
    _rel(vault, b, c, s)

    result = scan_typologies(vault)
    assert [m for m in result["matches"] if m["typology"] == "circular_ownership"] == []


def test_shared_intermediary(vault: Path) -> None:
    a, b, mid = (_entity(vault, t) for t in ("A", "B", "Mid"))
    s = _source(vault)
    _rel(vault, a, mid, s)
    _rel(vault, b, mid, s)

    result = scan_typologies(vault)

    shared = [m for m in result["matches"] if m["typology"] == "shared_intermediary"]
    assert len(shared) == 1
    assert mid in shared[0]["member_ids"]
    assert set(shared[0]["member_ids"]) == {a, b, mid}


def test_multi_hop_convergence(vault: Path) -> None:
    a, b, c, d = (_entity(vault, t) for t in ("A", "B", "C", "D"))
    s = _source(vault)
    # A → B → D and A → C → D: two distinct 2-hop directed paths A→D.
    _rel(vault, a, b, s, "owns")
    _rel(vault, b, d, s, "owns")
    _rel(vault, a, c, s, "advises")
    _rel(vault, c, d, s, "advises")

    result = scan_typologies(vault)

    convergence = [m for m in result["matches"] if m["typology"] == "multi_hop_convergence"]
    assert len(convergence) == 1
    assert set(convergence[0]["member_ids"]) == {a, d}


def test_scan_is_read_only_and_deterministic(vault: Path) -> None:
    a, b, c = (_entity(vault, t) for t in ("A", "B", "C"))
    s = _source(vault)
    _rel(vault, a, b, s)
    _rel(vault, b, c, s)
    before = {p.as_posix(): p.read_bytes() for p in vault.rglob("*") if p.is_file()}

    first = scan_typologies(vault)
    second = scan_typologies(vault)

    assert first == second
    after = {p.as_posix(): p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    assert before == after


def test_matches_carry_evidence_never_labels_without_it(vault: Path) -> None:
    a, b, mid = (_entity(vault, t) for t in ("A", "B", "Mid"))
    s = _source(vault)
    _rel(vault, a, mid, s)
    _rel(vault, b, mid, s)

    result = scan_typologies(vault)

    for match in result["matches"]:
        assert match["edge_ids"]
        assert match["source_ids"]
        assert match["member_ids"]
