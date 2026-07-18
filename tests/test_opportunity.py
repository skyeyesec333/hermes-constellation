"""Tests for Opportunity record type: model validation, CLI staging, review/promotion."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from constellation.models import (
    Opportunity,
    OpportunityStage,
    Sensitivity,
    generate_ulid,
)
from constellation.review import list_candidates, promote_candidate
from constellation.vault import initialize_vault


def _common(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "type": "opportunity",
        "title": "test-opportunity",
        "status": "active",
        "sensitivity": Sensitivity.INTERNAL,
        "subject_ids": [generate_ulid()],
        "created_at": datetime(2026, 7, 17, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 17, tzinfo=UTC),
    }
    values.update(overrides)
    return values


# ── Model validation ────────────────────────────────────────────────


def test_opportunity_requires_at_least_one_subject():
    obj = Opportunity(**_common(subject_ids=[generate_ulid()]))
    assert obj.type == "opportunity"
    assert obj.stage == OpportunityStage.TEST


def test_opportunity_rejects_empty_subject_ids():
    with pytest.raises(ValidationError):
        Opportunity(**_common(subject_ids=[]))


def test_opportunity_defaults_stage_to_test():
    obj = Opportunity(**_common(subject_ids=[generate_ulid()]))
    assert obj.stage == OpportunityStage.TEST


def test_opportunity_accepts_all_stages():
    for stage in OpportunityStage:
        obj = Opportunity(**_common(subject_ids=[generate_ulid()], stage=stage))
        assert obj.stage == stage


def test_opportunity_probability_is_bounded():
    Opportunity(**_common(subject_ids=[generate_ulid()], probability=0.0))
    Opportunity(**_common(subject_ids=[generate_ulid()], probability=1.0))
    with pytest.raises(ValidationError):
        Opportunity(**_common(subject_ids=[generate_ulid()], probability=-0.1))
    with pytest.raises(ValidationError):
        Opportunity(**_common(subject_ids=[generate_ulid()], probability=1.1))


def test_opportunity_next_action_due_requires_timezone():
    with pytest.raises(ValidationError):
        Opportunity(
            **_common(
                subject_ids=[generate_ulid()],
                next_action_due=datetime(2026, 8, 1),
            )
        )


def test_opportunity_supports_linked_claims_and_interactions():
    claim_id = generate_ulid()
    interaction_id = generate_ulid()
    decision_id = generate_ulid()
    source_id = generate_ulid()

    obj = Opportunity(
        **_common(
            subject_ids=[generate_ulid()],
            supporting_claims=[claim_id],
            feeding_interactions=[interaction_id],
            supporting_decisions=[decision_id],
            source_ids=[source_id],
            probability=0.6,
            expected_value="$500K-$1M",
            next_action="Schedule intro call",
        )
    )
    assert obj.supporting_claims == [claim_id]
    assert obj.feeding_interactions == [interaction_id]
    assert obj.supporting_decisions == [decision_id]
    assert obj.source_ids == [source_id]
    assert obj.probability == 0.6


def test_opportunity_stage_enum_values():
    assert OpportunityStage.TEST.value == "test"
    assert OpportunityStage.CLOSED_WON.value == "closed-won"
    assert OpportunityStage.CLOSED_LOST.value == "closed-lost"


# ── Review pipeline ──────────────────────────────────────────────────


def _stage_raw_opportunity(vault: Path) -> tuple[str, str]:
    """Stage a raw opportunity candidate and return (candidate_id, opportunity_ulid)."""
    from constellation.storage import atomic_write_text, safe_relative_path

    opp = Opportunity(
        **_common(
            subject_ids=[generate_ulid()],
            title="test-deal-opportunity",
            stage=OpportunityStage.QUALIFYING,
            probability=0.5,
            expected_value="$100K",
            next_action="Call next week",
        )
    )

    candidate_path = safe_relative_path(
        vault, Path(".constellation/candidates") / f"opportunity-{opp.id}.json"
    )
    atomic_write_text(
        vault,
        candidate_path.relative_to(vault),
        opp.model_dump_json(indent=2) + "\n",
    )
    return (candidate_path.stem, opp.id)


def test_opportunity_appears_in_review_list(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)

    cid, oid = _stage_raw_opportunity(vault)
    candidates = list_candidates(vault)

    opp_candidates = [c for c in candidates if c["id"] == cid]
    assert len(opp_candidates) == 1
    c = opp_candidates[0]
    assert c["kind"] == "opportunity_candidate"
    assert c["target_path"] == f"opportunities/{oid}.md"
    assert c["expected_base_hash"] is None
    assert c["promotable"] is True


def test_opportunity_promotes_to_canonical(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)

    cid, oid = _stage_raw_opportunity(vault)

    result = promote_candidate(vault, cid, confirm=True, expected_base_hash=None)
    assert result["status"] == "promoted"

    # Canonical file exists
    canonical = vault / "opportunities" / f"{oid}.md"
    assert canonical.is_file()
    content = canonical.read_text(encoding="utf-8")
    assert "test-deal-opportunity" in content
    assert "$100K" in content
    assert "qualifying" in content

    # Candidate packet was removed
    assert not (vault / ".constellation/candidates" / f"{cid}.json").exists()


def test_opportunity_promotion_rejects_non_null_hash(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)

    cid, _ = _stage_raw_opportunity(vault)

    with pytest.raises(Exception, match="create-only"):
        promote_candidate(vault, cid, confirm=True, expected_base_hash="a" * 64)


def test_opportunity_promotion_rejects_duplicate_target(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)

    from constellation.storage import atomic_write_text, safe_relative_path

    # Use a fixed ID to stage the same opportunity twice
    dup_id = generate_ulid()
    opp = Opportunity(
        **_common(
            subject_ids=[generate_ulid()],
            title="duplicate-test",
            id=dup_id,
        )
    )

    # First stage + promote
    cp1 = safe_relative_path(
        vault, Path(".constellation/candidates") / f"opportunity-{opp.id}.json"
    )
    atomic_write_text(
        vault, cp1.relative_to(vault), opp.model_dump_json(indent=2) + "\n"
    )
    promote_candidate(vault, cp1.stem, confirm=True, expected_base_hash=None)

    # Stage same ID again
    opp2 = Opportunity(
        **_common(
            subject_ids=[generate_ulid()],
            title="duplicate-test-v2",
            id=dup_id,
        )
    )
    cp2 = safe_relative_path(
        vault, Path(".constellation/candidates") / f"opportunity-{opp2.id}.json"
    )
    atomic_write_text(
        vault, cp2.relative_to(vault), opp2.model_dump_json(indent=2) + "\n"
    )

    with pytest.raises(Exception, match="already exists"):
        promote_candidate(vault, cp2.stem, confirm=True, expected_base_hash=None)


def test_opportunity_cli_staging_path(tmp_path: Path):
    """Exercise the CLI dispatch path for opportunity staging."""
    vault = tmp_path / "vault"
    initialize_vault(vault)

    from constellation.cli import run_action

    subject_id = generate_ulid()
    result = run_action(
        "opportunity",
        {
            "command": "opportunity",
            "vault": vault,
            "action": "stage",
            "subject_ids": [subject_id],
            "stage": "qualifying",
            "probability": 0.5,
            "expected_value": "$100K",
            "next_action": "Follow up next week",
            "supporting_claims": [generate_ulid()],
            "source_ids": [generate_ulid()],
        },
    )

    assert result["status"] == "staged"
    assert "opportunity_id" in result

    # Verify candidate packet exists
    candidate_path = vault / result["candidate_path"]
    assert candidate_path.is_file()
    packet = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert packet["type"] == "opportunity"
    assert packet["stage"] == "qualifying"


def test_opportunity_cli_list_is_filtered(tmp_path: Path):
    """opportunity list must return only opportunity candidates, not all candidates."""
    vault = tmp_path / "vault"
    initialize_vault(vault)

    from constellation.cli import run_action

    # Stage an opportunity
    run_action(
        "opportunity",
        {
            "command": "opportunity",
            "vault": vault,
            "action": "stage",
            "subject_ids": [generate_ulid()],
        },
    )

    # Also stage a claim (different type)
    from constellation.claim import stage_claim
    from datetime import datetime, timezone

    stage_claim(
        vault,
        subject_id=generate_ulid(),
        predicate="test-predicate",
        object_literal="test object",
        source_ids=[generate_ulid()],
        evidence_excerpt="test evidence",
        observed_at=datetime.now(timezone.utc),
    )

    # List should only return opportunities
    result = run_action(
        "opportunity",
        {
            "command": "opportunity",
            "vault": vault,
            "action": "list",
        },
    )

    assert isinstance(result, list)
    kinds = {c.get("kind") for c in result if isinstance(c, dict)}
    assert kinds == {"opportunity_candidate"}, f"Expected only opportunity_candidate, got {kinds}"
