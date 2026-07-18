import hashlib
import json
from datetime import UTC, datetime

import pytest

from constellation.claim import stage_claim
from constellation.frontmatter import render_frontmatter
from constellation.ingest import ingest_file
from constellation.models import CandidatePatch, Decision, Interaction, InteractionType, Sensitivity
from constellation.review import PromotionError, list_candidates, promote_candidate, write_candidate
from constellation.retrieval import exact_lookup
from constellation.storage import sha256_file
from constellation.validation import CanonicalValidationError, validate_canonical_text
from constellation.vault import initialize_vault

NOW = datetime(2026, 2, 3, tzinfo=UTC)
SOURCE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
CLAIM_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def claim_text(object_literal="Fictional Corp."):
    return render_frontmatter(
        {
            "schema_version": "0.1",
            "id": CLAIM_ID,
            "type": "claim",
            "title": "Fictional claim",
            "status": "active",
            "sensitivity": "internal",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "subject_id": SOURCE_ID,
            "predicate": "works_at",
            "object_literal": object_literal,
            "source_ids": [SOURCE_ID],
        },
        "Evidence summary.\n",
    )


def make_candidate(root, **changes):
    values = {
        "type": "candidate-patch",
        "title": "Claim candidate",
        "status": "pending-review",
        "sensitivity": Sensitivity.INTERNAL,
        "created_at": NOW,
        "updated_at": NOW,
        "target_path": "claims/fictional.md",
        "content": claim_text(),
    }
    values.update(changes)
    candidate = CandidatePatch(**values)
    return candidate, write_candidate(root, candidate)


def test_strict_canonical_frontmatter_validation():
    record = validate_canonical_text(claim_text(), "claims/example.md")
    assert record.id == CLAIM_ID
    invalid = claim_text().replace("sensitivity: internal", "sensitivity: secret")
    with pytest.raises(CanonicalValidationError):
        validate_canonical_text(invalid, "claims/example.md")
    with pytest.raises(CanonicalValidationError):
        validate_canonical_text(claim_text(), "misc/example.md")


def test_candidate_listing_and_explicit_confirmation(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    candidate, _ = make_candidate(root)
    listed = list_candidates(root)
    assert [item["id"] for item in listed] == [candidate.id]
    with pytest.raises(PromotionError, match="confirmation"):
        promote_candidate(root, candidate.id, confirm=False, expected_base_hash=None)
    assert not (root / candidate.target_path).exists()


def test_staged_claim_is_visible_to_generic_review_and_promotes(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    staged = stage_claim(
        root,
        subject_id=SOURCE_ID,
        predicate="works_at",
        object_literal="Fictional Corp.",
        source_ids=[SOURCE_ID],
        evidence_anchor="OCR:R0001",
        evidence_excerpt="Fictional source evidence.",
        confidence=0.9,
    )
    candidate_id = f"claim-{staged['claim_id']}"

    listed = list_candidates(root)

    assert listed == [
        {
            "id": candidate_id,
            "kind": "claim_candidate",
            "title": "Review claim: claim-01ARZ3ND-works_at",
            "target_path": f"claims/{staged['claim_id']}.md",
            "expected_base_hash": None,
            "promotable": True,
        }
    ]
    result = promote_candidate(root, candidate_id, confirm=True, expected_base_hash=None)

    target = root / f"claims/{staged['claim_id']}.md"
    assert result["status"] == "promoted"
    assert result["index_generation"]
    record = validate_canonical_text(target.read_text(encoding="utf-8"), target.relative_to(root))
    assert getattr(record, "id") == staged["claim_id"]
    assert not (root / ".constellation/candidates" / f"{candidate_id}.json").exists()


def test_staged_interaction_is_visible_to_generic_review_and_promotes(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    interaction = Interaction(
        type="interaction",
        title="Test interaction",
        status="review-required",
        sensitivity=Sensitivity.INTERNAL,
        interaction_type=InteractionType.MEETING,
        subject_ids=[SOURCE_ID],
        participants=[SOURCE_ID],
        channel="in-person",
        summary="Synthetic interaction evidence.",
        source_ids=[SOURCE_ID],
        occurred_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    candidate_id = f"interaction-{interaction.id}"
    candidate_path = root / ".constellation/candidates" / f"{candidate_id}.json"
    candidate_path.write_text(interaction.model_dump_json(indent=2) + "\n", encoding="utf-8")

    listed = list_candidates(root)

    assert listed == [
        {
            "id": candidate_id,
            "kind": "interaction_candidate",
            "title": "Review interaction: Test interaction",
            "target_path": f"interactions/{interaction.id}.md",
            "expected_base_hash": None,
            "promotable": True,
        }
    ]
    result = promote_candidate(root, candidate_id, confirm=True, expected_base_hash=None)

    target = root / f"interactions/{interaction.id}.md"
    assert result["status"] == "promoted"
    record = validate_canonical_text(target.read_text(encoding="utf-8"), target.relative_to(root))
    assert getattr(record, "type") == "interaction"
    assert not candidate_path.exists()


def test_staged_decision_is_visible_to_generic_review_and_promotes(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    decision = Decision(
        type="decision",
        title="Test decision",
        status="review-required",
        sensitivity=Sensitivity.INTERNAL,
        subject_id=SOURCE_ID,
        decision="Use the synthetic decision path.",
        rationale="Synthetic evidence.",
        source_ids=[SOURCE_ID],
        decided_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    candidate_id = f"decision-{decision.id}"
    candidate_path = root / ".constellation/candidates" / f"{candidate_id}.json"
    candidate_path.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")

    listed = list_candidates(root)

    assert listed == [
        {
            "id": candidate_id,
            "kind": "decision_candidate",
            "title": "Review decision: Test decision",
            "target_path": f"decisions/{decision.id}.md",
            "expected_base_hash": None,
            "promotable": True,
        }
    ]
    result = promote_candidate(root, candidate_id, confirm=True, expected_base_hash=None)

    target = root / f"decisions/{decision.id}.md"
    assert result["status"] == "promoted"
    record = validate_canonical_text(target.read_text(encoding="utf-8"), target.relative_to(root))
    assert getattr(record, "type") == "decision"
    assert not candidate_path.exists()


def test_staged_ingest_is_visible_and_promotion_rebuilds_index(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    source = root / "Inbox/Files/review-me.txt"
    source.write_text("Synthetic review fixture.\n", encoding="utf-8")
    ingested = ingest_file(root, source, now=NOW)
    candidate_id = ingested["candidate_id"]
    target = root / ingested["source_item_path"]

    listed = list_candidates(root)
    assert listed == [
        {
            "id": candidate_id,
            "kind": "candidate_patch",
            "title": "Ingest source: review-me",
            "target_path": ingested["source_item_path"],
            "expected_base_hash": None,
            "promotable": True,
        }
    ]
    with pytest.raises(PromotionError, match="expected base hash"):
        promote_candidate(
            root,
            candidate_id,
            confirm=True,
            expected_base_hash="0" * 64,
        )
    result = promote_candidate(
        root,
        candidate_id,
        confirm=True,
        expected_base_hash=None,
    )
    assert result["status"] == "promoted"
    assert result["index_generation"]
    assert target.is_file()
    assert not (root / ingested["candidate_path"]).exists()
    assert exact_lookup(root, ingested["source_id"])["status"] == "evidence_found"
    event = json.loads((root / ".constellation/action-ledger.jsonl").read_text(encoding="utf-8"))
    assert event["action"] == "candidate_promoted"


def test_legacy_ingest_candidate_remains_reviewable_and_reindexes(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    source_id = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
    target_relative = f"source-items/{source_id}.md"
    metadata = {
        "schema_version": "0.1",
        "id": source_id,
        "type": "source-item",
        "title": "Legacy pending source",
        "status": "active",
        "sensitivity": "internal",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "source_hash": "a" * 64,
        "original_path": "Library/Files/legacy.txt",
        "media_type": "text/plain",
    }
    target = root / target_relative
    target.write_text(render_frontmatter(metadata, "Legacy evidence.\n"), encoding="utf-8")
    candidate_id = f"ingest-{source_id}"
    packet = {
        "schema_version": "0.1",
        "kind": "ingest_candidate",
        "source_id": source_id,
        "source_hash": "a" * 64,
        "status": "pending_review",
    }
    candidate_path = root / f".constellation/candidates/{candidate_id}.json"
    candidate_path.write_text(json.dumps(packet), encoding="utf-8")
    expected_hash = sha256_file(target)

    listed = list_candidates(root)
    assert listed[0]["kind"] == "ingest_candidate"
    result = promote_candidate(
        root,
        candidate_id,
        confirm=True,
        expected_base_hash=expected_hash,
    )

    assert result["status"] == "reviewed"
    assert result["index_generation"]
    assert exact_lookup(root, source_id)["status"] == "evidence_found"
    assert not candidate_path.exists()


def test_promotion_is_atomic_validated_and_appends_ledger(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    candidate, _ = make_candidate(root)
    result = promote_candidate(root, candidate.id, confirm=True, expected_base_hash=None)
    target = root / candidate.target_path
    assert target.read_text(encoding="utf-8") == candidate.content
    assert result["status"] == "promoted"
    event = json.loads((root / ".constellation/action-ledger.jsonl").read_text(encoding="utf-8"))
    assert event["candidate_id"] == candidate.id
    assert not (root / ".constellation/candidates" / f"{candidate.id}.json").exists()


def test_promotion_rejects_conflict_invalid_schema_and_disallowed_folder(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    target = root / "claims/fictional.md"
    target.write_text(claim_text("Old"), encoding="utf-8")
    old_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    candidate, _ = make_candidate(root, expected_base_hash=old_hash)
    target.write_text(claim_text("Concurrent"), encoding="utf-8")
    with pytest.raises(PromotionError, match="conflict"):
        promote_candidate(root, candidate.id, confirm=True, expected_base_hash=old_hash)
    assert "Concurrent" in target.read_text(encoding="utf-8")

    bad, _ = make_candidate(root, target_path="Library/Files/bad.md")
    with pytest.raises(PromotionError):
        promote_candidate(root, bad.id, confirm=True, expected_base_hash=None)

    invalid, _ = make_candidate(root, content="---\nno: schema\n---\n")
    with pytest.raises(PromotionError):
        promote_candidate(root, invalid.id, confirm=True, expected_base_hash=None)
