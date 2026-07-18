"""Tests for Inquiry review/promotion pipeline (P0 defect: missing from review.py)."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.models import Inquiry, Sensitivity, generate_ulid
from constellation.review import list_candidates, promote_candidate
from constellation.vault import initialize_vault


def _stage_inquiry(vault: Path) -> tuple[str, str]:
    """Stage a raw inquiry candidate packet. Returns (candidate_id, inquiry_ulid)."""
    from constellation.storage import atomic_write_text, safe_relative_path

    inquiry = Inquiry(
        type="inquiry",
        title="test-inquiry-review-round-trip",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        question="What is the test question?",
        why_it_matters="Because tests need round trips.",
        target_scope="test",
        evidence_needed="test evidence",
        source_priority="primary",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    candidate_path = safe_relative_path(
        vault, Path(".constellation/candidates") / f"inquiry-{inquiry.id}.json"
    )
    atomic_write_text(
        vault,
        candidate_path.relative_to(vault),
        inquiry.model_dump_json(indent=2) + "\n",
    )
    return (candidate_path.stem, inquiry.id)


def test_inquiry_appears_in_review_list(tmp_path: Path):
    """Inquiry candidates must appear in the generic review listing."""
    vault = tmp_path / "vault"
    initialize_vault(vault)

    cid, iid = _stage_inquiry(vault)
    candidates = list_candidates(vault)

    inquiry_candidates = [c for c in candidates if c["id"] == cid]
    assert len(inquiry_candidates) == 1, (
        f"Expected 1 inquiry candidate, got {len(inquiry_candidates)}"
    )
    c = inquiry_candidates[0]
    assert c["kind"] == "inquiry_candidate"
    assert c["target_path"] == f"inquiries/{iid}.md"
    assert c["expected_base_hash"] is None
    assert c["promotable"] is True


def test_inquiry_promotes_to_canonical(tmp_path: Path):
    """Inquiry candidates must promote to canonical research/ notes."""
    vault = tmp_path / "vault"
    initialize_vault(vault)

    cid, iid = _stage_inquiry(vault)

    result = promote_candidate(vault, cid, confirm=True, expected_base_hash=None)
    assert result["status"] == "promoted"

    canonical = vault / "inquiries" / f"{iid}.md"
    assert canonical.is_file()
    content = canonical.read_text(encoding="utf-8")
    assert "test-inquiry-review-round-trip" in content
    assert "What is the test question?" in content

    assert not (vault / ".constellation/candidates" / f"{cid}.json").exists()


def test_inquiry_promotion_rejects_non_null_hash(tmp_path: Path):
    """Inquiry promotion must reject non-null expected_base_hash (create-only)."""
    vault = tmp_path / "vault"
    initialize_vault(vault)

    cid, _ = _stage_inquiry(vault)

    with pytest.raises(Exception, match="create-only"):
        promote_candidate(vault, cid, confirm=True, expected_base_hash="a" * 64)


def test_inquiry_promotion_rejects_duplicate_target(tmp_path: Path):
    """Promoting an inquiry to an existing target must fail."""
    vault = tmp_path / "vault"
    initialize_vault(vault)

    cid, _ = _stage_inquiry(vault)
    promote_candidate(vault, cid, confirm=True, expected_base_hash=None)

    # Stage again with same ID
    from constellation.storage import atomic_write_text, safe_relative_path

    dup_inquiry = Inquiry(
        type="inquiry",
        title="duplicate-inquiry",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        question="Duplicate?",
        why_it_matters="test",
        target_scope="test",
        evidence_needed="test",
        source_priority="primary",
        id=cid.replace("inquiry-", ""),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    cp = safe_relative_path(
        vault, Path(".constellation/candidates") / f"inquiry-{dup_inquiry.id}.json"
    )
    atomic_write_text(
        vault, cp.relative_to(vault), dup_inquiry.model_dump_json(indent=2) + "\n"
    )

    with pytest.raises(Exception, match="already exists"):
        promote_candidate(vault, cp.stem, confirm=True, expected_base_hash=None)


def test_inquiry_promotion_rejects_filename_mismatch(tmp_path: Path):
    """Inquiry candidate filename must match the inquiry ID."""
    vault = tmp_path / "vault"
    initialize_vault(vault)

    from constellation.storage import atomic_write_text, safe_relative_path

    inquiry = Inquiry(
        type="inquiry",
        title="mismatched",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        question="Test",
        why_it_matters="test",
        target_scope="test",
        evidence_needed="test",
        source_priority="primary",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Write with wrong filename
    wrong_id = generate_ulid()
    cp = safe_relative_path(
        vault, Path(".constellation/candidates") / f"inquiry-{wrong_id}.json"
    )
    atomic_write_text(
        vault, cp.relative_to(vault), inquiry.model_dump_json(indent=2) + "\n"
    )

    with pytest.raises(Exception, match="filename"):
        promote_candidate(vault, cp.stem, confirm=True, expected_base_hash=None)
