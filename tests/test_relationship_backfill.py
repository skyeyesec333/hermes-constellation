"""Tests for the conservative relationship backfill planner (Wave 2 Task 2.4)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.models import (
    Claim,
    EntityKind,
    EntityRecord,
    RelationshipRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.relationship_backfill import (
    BackfillError,
    backfill_inventory,
    backfill_plan,
    backfill_stage,
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


def _entity(vault: Path, title: str, kind=EntityKind.COMPANY) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=kind, title=title, status="active",
        sensitivity=Sensitivity.INTERNAL, source_ids=[], created_at=NOW, updated_at=NOW,
    )
    _write(vault, "people" if kind == EntityKind.PERSON else "entities", record)
    return record.id


def _source(vault: Path) -> str:
    record = SourceItem(
        id=generate_ulid(), type="source_item", title="Fictional filing",
        status="active", sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"bytes").hexdigest(),
        original_path="Library/Files/filing.pdf", media_type="application/pdf",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "source-items", record)
    return record.id


def _claim(vault: Path, subject: str, predicate: str, obj: str | None,
           sources: list[str], literal: str | None = None) -> str:
    record = Claim(
        id=generate_ulid(), title=f"claim {predicate}", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=subject, predicate=predicate,
        object_id=obj, object_literal=literal, source_ids=sources,
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "claims", record)
    return record.id


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_vault(root)
    return root


def test_inventory_counts_eligibility(vault: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    s1 = _source(vault)
    _claim(vault, a, "works_at", b, [s1])           # eligible (alias)
    _claim(vault, a, "owns", b, [s1])               # eligible (canonical)
    _claim(vault, a, "mystery_link", b, [s1])       # unrecognized predicate
    _claim(vault, a, "has_headquarters", None, [s1], literal="Zurich")  # no object_id

    result = backfill_inventory(vault)

    assert result["claims_entity_to_entity"] == 3
    assert result["eligible"] == 2
    assert result["ineligible"]["unrecognized_predicate"] == 1
    assert result["ineligible"]["literal_only"] == 1
    assert result["canonical_relationships"] == 0
    assert result["vault_inventory_hash"]


def test_plan_groups_and_deterministic(vault: Path, tmp_path: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    s1 = _source(vault)
    s2 = _source(vault)
    _claim(vault, a, "works_at", b, [s1])
    _claim(vault, a, "employed_by", b, [s2])  # same canonicalized assertion

    out = tmp_path / "plan.json"
    result = backfill_plan(vault, out)
    plan = json.loads(out.read_text(encoding="utf-8"))

    assert len(plan["proposals"]) == 1
    proposal = plan["proposals"][0]
    assert proposal["action"] == "create"
    assert proposal["predicate"] == "employed_by"
    assert sorted(proposal["source_ids"]) == sorted([s1, s2])
    assert len(proposal["claim_ids"]) == 2
    # Deterministic: byte-identical rerun.
    out2 = tmp_path / "plan2.json"
    backfill_plan(vault, out2)
    assert out.read_bytes() == out2.read_bytes()
    assert result["proposals"] == 1


def test_plan_marks_existing_relationship_as_corroborate(vault: Path, tmp_path: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    s1 = _source(vault)
    s2 = _source(vault)
    _write(vault, "relationships", RelationshipRecord(
        id=generate_ulid(), title="employs", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=a, predicate="employed_by",
        object_id=b, source_ids=[s1], evidence_class="user-asserted",
        created_at=NOW, updated_at=NOW,
    ))
    _claim(vault, a, "works_at", b, [s2])

    out = tmp_path / "plan.json"
    backfill_plan(vault, out)
    plan = json.loads(out.read_text(encoding="utf-8"))

    assert len(plan["proposals"]) == 1
    proposal = plan["proposals"][0]
    assert proposal["action"] == "corroborate"
    assert proposal["existing_relationship_id"]


def test_plan_reports_unresolved(vault: Path, tmp_path: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    ghost = generate_ulid()
    s1 = _source(vault)
    _claim(vault, a, "owns", b, [ghost])           # unresolvable source
    _claim(vault, ghost, "owns", b, [s1])          # unresolvable endpoint

    out = tmp_path / "plan.json"
    backfill_plan(vault, out)
    plan = json.loads(out.read_text(encoding="utf-8"))

    assert plan["proposals"] == []
    reasons = {item["reason"] for item in plan["unresolved"]}
    assert "unresolved_source" in reasons
    assert "unresolved_endpoint" in reasons


def test_stage_refuses_stale_plan(vault: Path, tmp_path: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    s1 = _source(vault)
    _claim(vault, a, "owns", b, [s1])
    out = tmp_path / "plan.json"
    backfill_plan(vault, out)
    _claim(vault, a, "directs", b, [s1])  # vault changed after planning

    with pytest.raises(BackfillError, match="stale"):
        backfill_stage(vault, out, limit=10)


def test_stage_bounded_idempotent_no_direct_writes(vault: Path, tmp_path: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    c = _entity(vault, "Gamma")
    s1 = _source(vault)
    _claim(vault, a, "owns", b, [s1])
    _claim(vault, a, "directs", c, [s1])
    out = tmp_path / "plan.json"
    backfill_plan(vault, out)

    first = backfill_stage(vault, out, limit=1)
    assert first["staged"] == 1
    candidates = list((vault / ".constellation" / "candidates").glob("relationship-*.json"))
    assert len(candidates) == 1
    # No canonical relationship written.
    assert list((vault / "relationships").glob("*.md")) == []
    # Rerun stages the remaining proposal, never duplicates the first.
    second = backfill_stage(vault, out, limit=10)
    assert second["staged"] == 1
    assert second["already_staged"] == 1
    candidates = list((vault / ".constellation" / "candidates").glob("relationship-*.json"))
    assert len(candidates) == 2
    third = backfill_stage(vault, out, limit=10)
    assert third["staged"] == 0
    assert third["already_staged"] == 2


def test_stage_corroborate_writes_candidate_patch(vault: Path, tmp_path: Path) -> None:
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    s1 = _source(vault)
    s2 = _source(vault)
    existing = RelationshipRecord(
        id=generate_ulid(), title="employs", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=a, predicate="employed_by",
        object_id=b, source_ids=[s1], evidence_class="user-asserted",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "relationships", existing)
    _claim(vault, a, "works_at", b, [s2])
    out = tmp_path / "plan.json"
    backfill_plan(vault, out)

    result = backfill_stage(vault, out, limit=10)

    assert result["corroborations_staged"] == 1
    patches = [
        p for p in (vault / ".constellation" / "candidates").glob("*.json")
        if json.loads(p.read_text(encoding="utf-8")).get("type") == "candidate_patch"
    ]
    assert len(patches) == 1
    patch = json.loads(patches[0].read_text(encoding="utf-8"))
    assert patch["target_path"] == f"relationships/{existing.id}.md"
    assert patch["expected_base_hash"] is not None
    metadata, _ = parse_frontmatter(patch["content"])
    assert sorted(metadata["source_ids"]) == sorted([s1, s2])
    # Canonical file itself untouched.
    metadata, _ = parse_frontmatter(
        (vault / "relationships" / f"{existing.id}.md").read_text(encoding="utf-8")
    )
    assert metadata["source_ids"] == [s1]
