"""Tests for review-only hypothesis packets (Wave 4 Task 4.2)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.hypotheses import (
    HypothesisError,
    generate_hypotheses,
    list_hypotheses,
    refresh_hypothesis,
    show_hypothesis,
)
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
    a, b, c = (_entity(root, t) for t in ("A", "B", "C"))
    s = _source(root)
    _rel(root, a, b, s)
    _rel(root, b, c, s)
    return root


def test_generate_creates_packets_idempotently(vault: Path) -> None:
    first = generate_hypotheses(vault)
    assert first["created"] == 1
    second = generate_hypotheses(vault)
    assert second["created"] == 0
    assert second["existing"] == 1
    packets = list((vault / ".constellation" / "hypotheses").glob("hyp-*.json"))
    assert len(packets) == 1
    packet = json.loads(packets[0].read_text(encoding="utf-8"))
    assert packet["kind"] == "hypothesis"
    assert packet["status"] == "open"
    assert packet["origin"]["typology"] == "layered_ownership"
    assert len(packet["evidence_for"]) <= 10
    assert packet["confidence_bounds"]["label"] == "unverified-lead"
    assert packet["falsification_checks"]
    assert packet["expires_at"] > packet["created_at"]
    assert packet["review_trail"][0]["event"] == "created"
    # Packets are derived artifacts: canonical folders untouched.
    assert len(list((vault / "relationships").glob("*.md"))) == 2


def test_list_and_show(vault: Path) -> None:
    generate_hypotheses(vault)
    listed = list_hypotheses(vault)
    assert len(listed) == 1
    shown = show_hypothesis(vault, listed[0]["id"])
    assert shown["id"] == listed[0]["id"]
    assert shown["subject_ids"]
    with pytest.raises(HypothesisError):
        show_hypothesis(vault, "hyp-nonexistent")


def test_refresh_refutes_when_evidence_disappears(vault: Path) -> None:
    generate_hypotheses(vault)
    packet_id = list_hypotheses(vault)[0]["id"]
    # Remove one explaining edge (simulating supersede-and-archive).
    first_rel = sorted((vault / "relationships").glob("*.md"))[0]
    first_rel.unlink()

    result = refresh_hypothesis(vault, packet_id)

    assert result["status"] == "refuted"
    packet = show_hypothesis(vault, packet_id)
    assert packet["status"] == "refuted"
    assert packet["review_trail"][-1]["event"] == "refreshed"
    assert packet["last_evaluation"]["evidence_missing"]


def test_refresh_marks_expired(vault: Path) -> None:
    generate_hypotheses(vault)
    packet_id = list_hypotheses(vault)[0]["id"]
    path = vault / ".constellation" / "hypotheses" / f"{packet_id}.json"
    packet = json.loads(path.read_text(encoding="utf-8"))
    packet["expires_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    path.write_text(json.dumps(packet, indent=2), encoding="utf-8")

    result = refresh_hypothesis(vault, packet_id)
    assert result["status"] == "expired"


def test_refresh_healthy_packet_stays_open(vault: Path) -> None:
    generate_hypotheses(vault)
    packet_id = list_hypotheses(vault)[0]["id"]
    result = refresh_hypothesis(vault, packet_id)
    assert result["status"] == "open"
    packet = show_hypothesis(vault, packet_id)
    assert packet["last_evaluation"]["evidence_missing"] == []
    assert len(packet["review_trail"]) == 2
