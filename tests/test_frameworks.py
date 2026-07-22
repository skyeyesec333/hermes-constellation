"""Tests for strategic framework execution."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frameworks import FrameworkError, run_framework
from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.models import Analysis, Claim, ClaimStatus, EntityKind, EntityRecord, Sensitivity, generate_ulid
from constellation.review import PromotionError, list_candidates, promote_candidate
from constellation.validation import validate_canonical_text
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


def _setup_vault(tmp_path: Path) -> tuple[Path, str]:
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
    return vault, entity.id


def test_porter_stages_analysis_candidate(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)

    result = run_framework(vault, entity_id, "porter_five_forces")
    assert result["status"] == "staged"
    assert result["framework"] == "porter_five_forces"
    assert result["entity_id"] == entity_id
    assert "analysis_id" in result
    assert "candidate_path" in result

    # Verify candidate file exists
    candidate = vault / result["candidate_path"]
    assert candidate.is_file()


def test_framework_envelope_keeps_body_and_only_entity_claim_evidence(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    supporting = Claim(
        id=generate_ulid(),
        type="claim",
        title="TestCo capability",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        subject_id=entity_id,
        predicate="has_capability",
        object_literal="Fictional capability",
        source_ids=[generate_ulid()],
        claim_status=ClaimStatus.SOURCE_CLAIMED,
        created_at=NOW,
        updated_at=NOW,
    )
    unrelated = supporting.model_copy(
        update={"id": generate_ulid(), "subject_id": generate_ulid(), "title": "TestCo noise"}
    )
    for claim in (supporting, unrelated):
        (vault / "claims" / f"{str(claim.id)}.md").write_text(
            render_frontmatter(claim.model_dump(mode="json", exclude_none=True), "Evidence body.\n"),
            encoding="utf-8",
        )

    result = run_framework(vault, entity_id, "porter_five_forces")
    candidate = json.loads((vault / result["candidate_path"]).read_text(encoding="utf-8"))

    assert candidate["kind"] == "analysis_candidate"
    assert candidate["analysis"]["supporting_claims"] == [supporting.id]
    assert candidate["body_markdown"].startswith("# Porter's Five Forces: TestCo")
    assert result["evidence_count"] == 1


def test_framework_ignores_claims_that_fail_canonical_validation(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    invalid_claim = Claim(
        id=generate_ulid(),
        type="claim",
        title="Empty evidence body",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        subject_id=entity_id,
        predicate="has_capability",
        object_literal="Fictional capability",
        source_ids=[generate_ulid()],
        claim_status=ClaimStatus.SOURCE_CLAIMED,
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "claims" / f"{str(invalid_claim.id)}.md").write_text(
        render_frontmatter(invalid_claim.model_dump(mode="json", exclude_none=True), ""),
        encoding="utf-8",
    )

    result = run_framework(vault, entity_id, "swot")
    candidate = json.loads((vault / result["candidate_path"]).read_text(encoding="utf-8"))

    assert result["evidence_count"] == 0
    assert candidate["analysis"]["supporting_claims"] == []


def test_analysis_envelope_promotion_preserves_generated_body(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    staged = run_framework(vault, entity_id, "swot")

    promoted = promote_candidate(
        vault,
        f"analysis-{staged['analysis_id']}",
        confirm=True,
        expected_base_hash=None,
    )

    target_path = str(promoted["target_path"])
    text = (vault / target_path).read_text(encoding="utf-8")
    assert "# SWOT Analysis: TestCo" in text
    assert "## Strengths (Internal)" in text
    assert "Operator review required" in text
    validate_canonical_text(text, target_path)


def test_analysis_envelope_requires_evidence_status(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    staged = run_framework(vault, entity_id, "swot")
    candidate_path = vault / str(staged["candidate_path"])
    packet = json.loads(candidate_path.read_text(encoding="utf-8"))
    packet.pop("evidence_status")
    candidate_path.write_text(json.dumps(packet), encoding="utf-8")

    assert list_candidates(vault) == []
    with pytest.raises(PromotionError, match="evidence status"):
        promote_candidate(vault, f"analysis-{staged['analysis_id']}", confirm=True, expected_base_hash=None)


def test_analysis_envelope_preserves_narrative_body_bytes(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    staged = run_framework(vault, entity_id, "swot")
    candidate_path = vault / str(staged["candidate_path"])
    packet = json.loads(candidate_path.read_text(encoding="utf-8"))
    expected_body = "# Narrative with intentional trailing spaces  \n\n"
    packet["body_markdown"] = expected_body
    candidate_path.write_text(json.dumps(packet), encoding="utf-8")

    promoted = promote_candidate(vault, f"analysis-{staged['analysis_id']}", confirm=True, expected_base_hash=None)
    _, body = parse_frontmatter((vault / str(promoted["target_path"])).read_text(encoding="utf-8"))
    assert body == expected_body


def test_legacy_raw_analysis_candidate_remains_listable_and_promotable(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    legacy = Analysis(
        id=generate_ulid(),
        title="Legacy TestCo SWOT",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        framework="swot",
        entity_id=entity_id,
        created_at=NOW,
        updated_at=NOW,
    )
    candidate_id = f"analysis-{legacy.id}"
    (vault / ".constellation/candidates" / f"{candidate_id}.json").write_text(
        legacy.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    listed = list_candidates(vault)
    assert listed[0]["id"] == candidate_id
    assert "legacy analysis" in str(listed[0]["title"]).casefold()
    promoted = promote_candidate(vault, candidate_id, confirm=True, expected_base_hash=None)
    text = (vault / str(promoted["target_path"])).read_text(encoding="utf-8")
    assert "Legacy Analysis candidate: no narrative body was staged." in text


def test_swot_stages_analysis_candidate(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)

    result = run_framework(vault, entity_id, "swot")
    assert result["status"] == "staged"
    assert result["framework"] == "swot"


def test_unknown_framework_rejected(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    with pytest.raises(FrameworkError, match="unsupported framework"):
        run_framework(vault, entity_id, "pestle")


def test_missing_entity_rejected(tmp_path: Path) -> None:
    vault, _ = _setup_vault(tmp_path)
    with pytest.raises(FrameworkError, match="entity not found"):
        run_framework(vault, "nonexistent", "porter_five_forces")


def test_analysis_candidate_is_review_required(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    result = run_framework(vault, entity_id, "porter_five_forces")

    candidate_data = json.loads((vault / result["candidate_path"]).read_text())
    assert candidate_data["analysis"]["operator_reviewed"] is False
    assert candidate_data["analysis"]["confidence"] == "low"
    assert candidate_data["evidence_status"] == "insufficient_evidence"
    assert "No direct claims in vault." in candidate_data["body_markdown"]


def test_analyze_cli_args() -> None:
    from constellation.cli import build_parser

    values = vars(
        build_parser().parse_args([
            "analyze", "/tmp/vault", "porter",
            "--entity-id", "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        ])
    )
    assert values["framework"] == "porter"
    assert values["entity_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
