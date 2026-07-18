"""Tests for OSINT entity classification."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.cli import build_parser, run_action
from constellation.models import Classification, EntityCategory, EntityKind, EntityRecord, Sensitivity, generate_ulid
from constellation.frontmatter import render_frontmatter
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


# ── Model tests ────────────────────────────────────────────────────────

def test_classification_model_accepts_valid_category() -> None:
    c = Classification(
        id=generate_ulid(),
        title="Buyer classification for Entity",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=NOW,
        updated_at=NOW,
        category="buyer",
        entity_id=generate_ulid(),
        methodology="OSINT review",
        rationale="Evidence supports buyer classification",
    )
    assert c.category == "buyer"
    assert c.confidence == "medium"


def test_classification_rejects_invalid_category() -> None:
    with pytest.raises(ValueError, match="category must be one of"):
        Classification(
            id=generate_ulid(),
            title="Bad",
            status="active",
            sensitivity=Sensitivity.INTERNAL,
            created_at=NOW,
            updated_at=NOW,
            category="invalid",
            entity_id=generate_ulid(),
            methodology="test",
            rationale="test",
        )


def test_classification_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        Classification(
            id=generate_ulid(),
            title="Bad",
            status="active",
            sensitivity=Sensitivity.INTERNAL,
            created_at=NOW,
            updated_at=NOW,
            category="competitor",
            entity_id=generate_ulid(),
            methodology="test",
            rationale="test",
            confidence="extreme",
        )


def test_classification_all_valid_categories() -> None:
    for cat in ("buyer", "partner", "channel", "competitor", "false_lead"):
        c = Classification(
            id=generate_ulid(),
            title=f"{cat} classification",
            status="active",
            sensitivity=Sensitivity.INTERNAL,
            created_at=NOW,
            updated_at=NOW,
            category=cat,
            entity_id=generate_ulid(),
            methodology="test",
            rationale="test",
        )
        assert c.category == cat


def test_entity_category_enum() -> None:
    assert EntityCategory.BUYER.value == "buyer"
    assert EntityCategory.COMPETITOR.value == "competitor"
    assert EntityCategory.FALSE_LEAD.value == "false_lead"
    assert len(list(EntityCategory)) == 5


# ── CLI tests ───────────────────────────────────────────────────────────

def test_classify_cli_args() -> None:
    values = vars(
        build_parser().parse_args([
            "classify", "/tmp/vault", "stage",
            "--entity-id", "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "--category", "partner",
            "--methodology", "LinkedIn profile review",
            "--confidence", "high",
            "--rationale", "Multiple partnership indicators",
        ])
    )
    assert values["classify_action"] == "stage"
    assert values["entity_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert values["category"] == "partner"


def test_classify_list_cli_args() -> None:
    values = vars(
        build_parser().parse_args([
            "classify", "/tmp/vault", "list",
        ])
    )
    assert values["classify_action"] == "list"


# ── Stage via CLI ──────────────────────────────────────────────────────

def test_classify_stage_creates_candidate(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    entity = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.ORGANIZATION,
        title="TestCo",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "entities" / f"{entity.id}.md").write_text(
        render_frontmatter(entity.model_dump(mode="json", exclude_none=True), "# TestCo\n"),
        encoding="utf-8",
    )

    result = run_action("classify", {
        "vault": vault,
        "classify_action": "stage",
        "entity_id": entity.id,
        "category": "buyer",
        "methodology": "OSINT review of public filings",
        "confidence": "high",
        "rationale": "Evidence of procurement relationship",
        "supporting_claim_ids": [],
        "supporting_source_ids": [],
    })

    assert result["status"] == "staged"
    assert "classification_id" in result
    assert "candidate_path" in result

    # Verify candidate file exists
    candidate_path = vault / result["candidate_path"]
    assert candidate_path.is_file()


# ── List via CLI ───────────────────────────────────────────────────────

def test_classify_list_returns_candidates(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    entity = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.ORGANIZATION,
        title="TestCo",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "entities" / f"{entity.id}.md").write_text(
        render_frontmatter(entity.model_dump(mode="json", exclude_none=True), "# TestCo\n"),
        encoding="utf-8",
    )

    # Stage one
    run_action("classify", {
        "vault": vault,
        "classify_action": "stage",
        "entity_id": entity.id,
        "category": "competitor",
        "methodology": "Market analysis",
        "confidence": "medium",
        "rationale": "Overlapping product lines",
        "supporting_claim_ids": [],
        "supporting_source_ids": [],
    })

    result = run_action("classify", {
        "vault": vault,
        "classify_action": "list",
    })

    assert isinstance(result, list)
    assert len(result) == 1
