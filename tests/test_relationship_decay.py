"""Tests for relationship decay reports and staged suggestions (Wave 6 Task 6.3)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.models import (
    EntityKind,
    EntityRecord,
    RelationshipRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.relationship_decay import (
    RelationshipDecayError,
    decay_report,
    stage_decay_suggestions,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


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


def _rel(vault: Path, subject: str, obj: str, source: str, predicate: str = "owns",
         confidence: float | None = 0.9, **kwargs) -> str:
    updated_at = kwargs.pop("updated_at", NOW)
    created_at = kwargs.pop("created_at", min(NOW, updated_at))
    record = RelationshipRecord(
        id=generate_ulid(), title=predicate, status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=subject, object_id=obj,
        predicate=predicate, source_ids=[source], evidence_class="corroborated",
        confidence=confidence, created_at=created_at, updated_at=updated_at, **kwargs,
    )
    _write(vault, "relationships", record)
    return record.id


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_vault(root)
    return root


def test_report_temporal_states_and_counts(vault: Path) -> None:
    a, b, c = (_entity(vault, t) for t in ("A", "B", "C"))
    s = _source(vault)
    _rel(vault, a, b, s)  # active, no interval
    _rel(vault, b, c, s, valid_to=datetime(2020, 1, 1, tzinfo=timezone.utc))  # expired

    result = decay_report(vault, as_of=AS_OF)

    assert result["status"] == "ok"
    assert result["counts"]["total"] == 2
    assert result["counts"]["expired"] == 1
    assert result["counts"]["undated"] == 1
    states = {e["relationship_id"]: e["temporal_state"] for e in result["entries"]}
    assert "expired" in states.values()
    assert "undated" in states.values()


def test_report_confidence_decay_by_stability(vault: Path) -> None:
    a, b, c = (_entity(vault, t) for t in ("A", "B", "C"))
    s = _source(vault)
    # owns=durable (365d half-life), employed_by=standard (90d), met_with=transient (14d)
    old = NOW - timedelta(days=365)
    _rel(vault, a, b, s, predicate="owns", confidence=1.0, last_seen=old, updated_at=old)
    _rel(vault, b, c, s, predicate="employed_by", confidence=1.0, last_seen=old, updated_at=old)

    result = decay_report(vault, as_of=AS_OF)

    by_pred = {e["predicate"]: e for e in result["entries"]}
    durable = by_pred["owns"]["suggested_confidence"]
    standard = by_pred["employed_by"]["suggested_confidence"]
    assert durable is not None and standard is not None
    assert 0.4 < durable < 0.6          # ~one half-life elapsed
    assert standard < 0.1               # ~four half-lives elapsed
    assert result["counts"]["suggestions"] >= 1


def test_report_is_read_only(vault: Path) -> None:
    a, b = _entity(vault, "A"), _entity(vault, "B")
    s = _source(vault)
    _rel(vault, a, b, s, valid_to=datetime(2020, 1, 1, tzinfo=timezone.utc))
    before = {p.as_posix(): p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    decay_report(vault, as_of=AS_OF)
    after = {p.as_posix(): p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    assert before == after


def test_stage_suggestions_writes_candidate_patches_only(vault: Path) -> None:
    a, b, c = (_entity(vault, t) for t in ("A", "B", "C"))
    s = _source(vault)
    expired_id = _rel(vault, a, b, s, valid_to=datetime(2020, 1, 1, tzinfo=timezone.utc))
    _rel(vault, b, c, s)  # healthy, no suggestion

    result = stage_decay_suggestions(vault, as_of=AS_OF, limit=10)

    assert result["staged"] == 1
    patches = [
        p for p in (vault / ".constellation" / "candidates").glob("*.json")
        if json.loads(p.read_text(encoding="utf-8")).get("type") == "candidate_patch"
    ]
    assert len(patches) == 1
    patch = json.loads(patches[0].read_text(encoding="utf-8"))
    assert patch["target_path"] == f"relationships/{expired_id}.md"
    assert patch["expected_base_hash"] is not None
    metadata, _ = parse_frontmatter(patch["content"])
    assert metadata["status"] == "stale"
    # Canonical file untouched by staging.
    metadata, _ = parse_frontmatter(
        (vault / "relationships" / f"{expired_id}.md").read_text(encoding="utf-8")
    )
    assert metadata["status"] == "active"
    # Idempotent rerun.
    again = stage_decay_suggestions(vault, as_of=AS_OF, limit=10)
    assert again["staged"] == 0
    assert again["already_pending"] == 1


def test_stage_bounded_and_requires_initialized(vault: Path, tmp_path: Path) -> None:
    with pytest.raises(RelationshipDecayError):
        stage_decay_suggestions(vault, as_of=AS_OF, limit=0)
    with pytest.raises(RelationshipDecayError):
        decay_report(tmp_path / "nope")
