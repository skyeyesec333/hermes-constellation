"""Tests for OSINT entity classification."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.classification import ClassificationError, stage_classification
from constellation.cli import build_parser, run_action
from constellation.models import (
    Classification,
    Claim,
    EntityCategory,
    EntityKind,
    EntityRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.frontmatter import render_frontmatter
from constellation.review import list_candidates, promote_candidate
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


# ── Evidence-backed staging lifecycle ────────────────────────────────────


def _lifecycle_vault(tmp_path: Path) -> tuple[Path, str, str, str]:
    """Vault with one canonical entity, one canonical source, one canonical claim."""
    vault = tmp_path / "vault"
    initialize_vault(vault)

    entity = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.ORGANIZATION,
        title="EvidenceCo",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "entities" / f"{entity.id}.md").write_text(
        render_frontmatter(entity.model_dump(mode="json", exclude_none=True), "# EvidenceCo\n"),
        encoding="utf-8",
    )

    source = SourceItem(
        id=generate_ulid(),
        type="source_item",
        title="Evidence source",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_hash="a" * 64,
        original_path="Library/Files/evidence.txt",
        media_type="text/plain",
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "source-items" / f"{source.id}.md").write_text(
        render_frontmatter(source.model_dump(mode="json", exclude_none=True), "# Source\n"),
        encoding="utf-8",
    )

    claim = Claim(
        id=generate_ulid(),
        title="Competes in market",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        subject_id=entity.id,
        predicate="competes_in",
        object_literal="field intelligence",
        source_ids=[source.id],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "claims" / f"{claim.id}.md").write_text(
        render_frontmatter(claim.model_dump(mode="json", exclude_none=True), "# Claim\n"),
        encoding="utf-8",
    )
    return vault, entity.id, source.id, claim.id


def test_stage_classification_fails_closed_for_missing_entity(tmp_path: Path) -> None:
    vault, _, source_id, claim_id = _lifecycle_vault(tmp_path)
    missing_entity = generate_ulid()

    with pytest.raises(ClassificationError, match="canonical entity not found"):
        stage_classification(
            vault,
            entity_id=missing_entity,
            category="competitor",
            methodology="OSINT review",
            rationale="test rationale",
            supporting_claim_ids=[claim_id],
            supporting_source_ids=[source_id],
        )


def test_stage_classification_fails_closed_for_missing_claim_evidence(tmp_path: Path) -> None:
    vault, entity_id, source_id, _ = _lifecycle_vault(tmp_path)
    missing_claim = generate_ulid()

    with pytest.raises(ClassificationError, match="canonical claim not found"):
        stage_classification(
            vault,
            entity_id=entity_id,
            category="competitor",
            methodology="OSINT review",
            rationale="test rationale",
            supporting_claim_ids=[missing_claim],
            supporting_source_ids=[source_id],
        )


def test_stage_classification_fails_closed_for_missing_source_evidence(tmp_path: Path) -> None:
    vault, entity_id, _, claim_id = _lifecycle_vault(tmp_path)
    missing_source = generate_ulid()

    with pytest.raises(ClassificationError, match="canonical source item not found"):
        stage_classification(
            vault,
            entity_id=entity_id,
            category="competitor",
            methodology="OSINT review",
            rationale="test rationale",
            supporting_claim_ids=[claim_id],
            supporting_source_ids=[missing_source],
        )


def test_stage_classification_zero_evidence_is_marked_unsupported(tmp_path: Path) -> None:
    vault, entity_id, _, _ = _lifecycle_vault(tmp_path)

    result = stage_classification(
        vault,
        entity_id=entity_id,
        category="competitor",
        methodology="OSINT review",
        rationale="Initial hypothesis pending evidence collection",
    )

    assert result["status"] == "staged"
    assert result["evidence_status"] == "unsupported"
    candidate = json.loads((vault / str(result["candidate_path"])).read_text(encoding="utf-8"))
    assert candidate["supporting_claim_ids"] == []
    assert candidate["supporting_source_ids"] == []
    assert candidate["status"] == "review-required"


def test_stage_classification_preserves_evidence_ids(tmp_path: Path) -> None:
    vault, entity_id, source_id, claim_id = _lifecycle_vault(tmp_path)

    result = stage_classification(
        vault,
        entity_id=entity_id,
        category="competitor",
        methodology="OSINT review",
        rationale="Claim and source support competitor reading",
        supporting_claim_ids=[claim_id],
        supporting_source_ids=[source_id],
        confidence="low",
    )

    assert result["status"] == "staged"
    assert result["evidence_status"] == "evidence-backed"
    candidate = json.loads((vault / str(result["candidate_path"])).read_text(encoding="utf-8"))
    assert candidate["supporting_claim_ids"] == [claim_id]
    assert candidate["supporting_source_ids"] == [source_id]
    assert candidate["methodology"] == "OSINT review"
    assert candidate["confidence"] == "low"
    assert candidate["entity_id"] == entity_id


def test_classification_lifecycle_stage_list_promote_readback(tmp_path: Path) -> None:
    vault, entity_id, source_id, claim_id = _lifecycle_vault(tmp_path)

    staged = stage_classification(
        vault,
        entity_id=entity_id,
        category="competitor",
        methodology="OSINT review",
        rationale="Evidence-backed competitor classification",
        supporting_claim_ids=[claim_id],
        supporting_source_ids=[source_id],
    )
    classification_id = str(staged["classification_id"])

    listed = list_candidates(vault)
    matches = [c for c in listed if isinstance(c, dict) and classification_id in str(c)]
    assert matches, "staged classification must appear in review list"

    promoted = promote_candidate(
        vault, f"classification-{classification_id}", confirm=True, expected_base_hash=None
    )
    assert promoted["status"] == "promoted"
    assert str(promoted["target_path"]).startswith("classifications/")

    canonical_path = vault / "classifications" / f"{classification_id}.md"
    assert canonical_path.is_file()
    text = canonical_path.read_text(encoding="utf-8")
    assert claim_id in text
    assert source_id in text
    assert entity_id in text


def test_classify_cli_stage_uses_validated_staging(tmp_path: Path) -> None:
    vault, entity_id, source_id, claim_id = _lifecycle_vault(tmp_path)
    missing_entity = generate_ulid()
    values = vars(
        build_parser().parse_args(
            [
                "classify",
                str(vault),
                "stage",
                "--entity-id",
                missing_entity,
                "--category",
                "competitor",
                "--methodology",
                "OSINT review",
                "--rationale",
                "CLI staging must validate the entity",
            ]
        )
    )

    with pytest.raises(ClassificationError, match="canonical entity not found"):
        run_action(str(values.pop("command")), values)


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
