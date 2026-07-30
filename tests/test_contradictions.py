"""Tests for Stage 7.3 contradiction detection + resolution proposals.

Contract: contradictions (same subject+predicate, conflicting object) are
detected deterministically and staged as REVIEW-ONLY candidates — nothing
auto-resolves. Promotion is the human pick: the proposed winner supersedes
each loser via the 7.1 edge (loser stale, ledgered, audit trail complete).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from constellation.contradictions import (
    ContradictionError,
    detect_contradictions,
    stage_contradiction_candidate,
)
from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.models import (
    Claim,
    ClaimStatus,
    EntityKind,
    EntityRecord,
    Sensitivity,
    generate_ulid,
)
from constellation.review import list_candidates, promote_candidate
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _write(vault: Path, folder: str, record) -> str:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n"),
        encoding="utf-8",
    )
    return record.id


def _claim(claim_id: str, entity_id: str, literal: str, **kwargs) -> Claim:
    if "claim_status" in kwargs and isinstance(kwargs["claim_status"], str):
        kwargs["claim_status"] = ClaimStatus(kwargs["claim_status"])
    fields = dict(
        id=claim_id,
        type="claim",
        title=f"headcount {literal}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[entity_id],
        created_at=NOW,
        updated_at=NOW,
        subject_id=entity_id,
        predicate="headcount",
        object_literal=literal,
    )
    fields.update(kwargs)
    return Claim(**fields)


def _vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="ContrCo",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    return vault, _write(vault, "entities", entity)


def _read(vault: Path, claim_id: str) -> dict:
    meta, _ = parse_frontmatter((vault / "claims" / f"{claim_id}.md").read_text(encoding="utf-8"))
    return meta


def test_detects_conflicting_objects_same_subject_predicate(tmp_path: Path) -> None:
    vault, entity_id = _vault(tmp_path)
    _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))

    proposals = detect_contradictions(vault)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["subject_id"] == entity_id
    assert proposal["predicate"] == "headcount"
    assert len(proposal["claims"]) == 2
    assert proposal["winner_id"] in {c["id"] for c in proposal["claims"]}
    assert proposal["loser_ids"]


def test_same_object_is_not_a_contradiction(tmp_path: Path) -> None:
    vault, entity_id = _vault(tmp_path)
    _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))

    assert detect_contradictions(vault) == []


def test_stale_claims_are_excluded(tmp_path: Path) -> None:
    from constellation.supersedes import supersede_claim

    vault, entity_id = _vault(tmp_path)
    old_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    new_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))
    supersede_claim(vault, new_id, old_id, actor="test", basis=["s1"])

    assert detect_contradictions(vault) == []


def test_authority_outranks_recency_and_support_counts(tmp_path: Path) -> None:
    vault, entity_id = _vault(tmp_path)
    older_corroborated = _write(vault, "claims", _claim(
        generate_ulid(), entity_id, "120",
        claim_status="corroborated",
        created_at=NOW - timedelta(days=10),
        updated_at=NOW - timedelta(days=10),
    ))
    _write(vault, "claims", _claim(
        generate_ulid(), entity_id, "135",
        claim_status="source-claimed",  # newer but weaker authority
    ))

    proposal = detect_contradictions(vault)[0]

    assert proposal["winner_id"] == older_corroborated
    winner = [c for c in proposal["claims"] if c["id"] == older_corroborated][0]
    assert winner["rank_basis"]["authority"] == "corroborated"


def test_support_count_breaks_ties(tmp_path: Path) -> None:
    vault, entity_id = _vault(tmp_path)
    supported = _write(vault, "claims", _claim(
        generate_ulid(), entity_id, "120",
        source_ids=[generate_ulid(), generate_ulid(), generate_ulid()],
    ))
    _write(vault, "claims", _claim(generate_ulid(), entity_id, "135", source_ids=[generate_ulid()]))

    proposal = detect_contradictions(vault)[0]

    assert proposal["winner_id"] == supported


def test_stage_writes_review_only_candidate(tmp_path: Path) -> None:
    vault, entity_id = _vault(tmp_path)
    _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))

    result = stage_contradiction_candidate(vault, entity_id, "headcount", actor="cso")

    assert result["status"] == "staged"
    packet = vault / ".constellation" / "candidates" / f"contradiction-{result['candidate_id']}.json"
    assert packet.is_file()
    listed = list_candidates(vault)
    assert len(listed) == 1
    assert listed[0]["kind"] == "contradiction_candidate"
    assert listed[0]["promotable"] is True
    # nothing resolved yet: both claims still live
    for claim in result["claims"]:
        assert _read(vault, claim["id"])["claim_status"] != "stale"


def test_stage_without_contradiction_fails_closed(tmp_path: Path) -> None:
    vault, entity_id = _vault(tmp_path)
    _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))

    with pytest.raises(ContradictionError, match="no contradiction"):
        stage_contradiction_candidate(vault, entity_id, "headcount", actor="cso")


def test_promotion_writes_supersedes_edges_and_audit_trail(tmp_path: Path) -> None:
    vault, entity_id = _vault(tmp_path)
    loser_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    winner_id = _write(vault, "claims", _claim(
        generate_ulid(), entity_id, "135", claim_status="corroborated",
    ))
    staged = stage_contradiction_candidate(vault, entity_id, "headcount", actor="cso")

    result = promote_candidate(
        vault, staged["candidate_ref"], confirm=True, expected_base_hash=None
    )

    assert result["status"] == "promoted"
    assert _read(vault, loser_id)["claim_status"] == "stale"
    assert winner_id and _read(vault, winner_id)["claim_status"] == "corroborated"
    assert loser_id in (_read(vault, winner_id).get("supersedes") or [])
    # audit trail: 7.1 ledger + candidate removed
    ledger = (vault / ".constellation" / "supersedes-ledger.jsonl").read_text(encoding="utf-8")
    assert loser_id in ledger and winner_id in ledger
    assert not (vault / ".constellation" / "candidates" / f"contradiction-{staged['candidate_id']}.json").exists()
    from constellation.validation import validate_vault
    assert validate_vault(vault)["invalid"] == 0


def test_promotion_fails_closed_on_missing_claim(tmp_path: Path) -> None:
    from constellation.review import PromotionError

    vault, entity_id = _vault(tmp_path)
    loser_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))
    staged = stage_contradiction_candidate(vault, entity_id, "headcount", actor="cso")
    (vault / "claims" / f"{loser_id}.md").unlink()  # tamper after staging

    with pytest.raises(PromotionError):
        promote_candidate(vault, staged["candidate_ref"], confirm=True, expected_base_hash=None)
