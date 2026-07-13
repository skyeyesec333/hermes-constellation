import hashlib
import json
from datetime import UTC, datetime

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.ingest import ingest_file
from constellation.models import CandidatePatch, Sensitivity
from constellation.review import PromotionError, list_candidates, promote_candidate, write_candidate
from constellation.storage import sha256_file
from constellation.validation import CanonicalValidationError, validate_canonical_text
from constellation.vault import initialize_vault

NOW = datetime(2026, 2, 3, tzinfo=UTC)
SOURCE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
CLAIM_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def claim_text(statement="Supported fictional statement."):
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
            "statement": statement,
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


def test_ingest_candidate_is_visible_and_can_be_conflict_safely_accepted(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    source = root / "Inbox/Files/review-me.txt"
    source.write_text("Synthetic review fixture.\n", encoding="utf-8")
    ingested = ingest_file(root, source, now=NOW)
    candidate_id = f"ingest-{ingested['source_id']}"
    target = root / ingested["source_item_path"]
    expected_hash = sha256_file(target)

    listed = list_candidates(root)
    assert listed == [
        {
            "id": candidate_id,
            "kind": "ingest_candidate",
            "title": "Ingest review: review-me",
            "target_path": ingested["source_item_path"],
            "expected_base_hash": expected_hash,
            "promotable": True,
        }
    ]
    with pytest.raises(PromotionError, match="base hash conflict"):
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
        expected_base_hash=expected_hash,
    )
    assert result["status"] == "reviewed"
    assert not (root / ingested["candidate_path"]).exists()
    event = json.loads((root / ".constellation/action-ledger.jsonl").read_text(encoding="utf-8"))
    assert event["action"] == "ingest_candidate_reviewed"


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
