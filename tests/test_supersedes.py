"""Tests for Stage 7.1 supersedes links.

Contract: superseding is a journaled, reversible, review-aware canonical
mutation — the old claim is preserved with lifecycle state ``stale`` (never
deleted, never hidden), the new claim carries the typed ``supersedes`` edge,
and every application is ledgered with actor + basis + hashes for rollback.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.models import (
    Claim,
    EntityKind,
    EntityRecord,
    Sensitivity,
    generate_ulid,
)
from constellation.supersedes import (
    SupersedesError,
    supersede_chain,
    supersede_claim,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)  # past date: rewritten updated_at (real now) stays after created_at


def _write(vault: Path, folder: str, record) -> str:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n"),
        encoding="utf-8",
    )
    return record.id


def _claim(claim_id: str, entity_id: str, literal: str, **kwargs) -> Claim:
    return Claim(
        id=claim_id,
        type="claim",
        title=f"claim {literal}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[entity_id],  # fixture: any ULID satisfies min_length=1
        created_at=NOW,
        updated_at=NOW,
        subject_id=entity_id,
        predicate="headcount",
        object_literal=literal,
        **kwargs,
    )


def _vault_with_entity(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.COMPANY,
        title="ChainCo",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    return vault, _write(vault, "entities", entity)


def _read_claim(vault: Path, claim_id: str) -> dict:
    metadata, _ = parse_frontmatter((vault / "claims" / f"{claim_id}.md").read_text(encoding="utf-8"))
    return metadata


def test_supersede_marks_old_stale_and_links_new(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    old_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    new_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))

    result = supersede_claim(vault, new_id, old_id, actor="cso", basis=["src-1"])

    assert result["status"] == "applied"
    old_meta = _read_claim(vault, old_id)
    new_meta = _read_claim(vault, new_id)
    assert old_meta["claim_status"] == "stale"  # preserved, not deleted
    assert (vault / "claims" / f"{old_id}.md").is_file()
    assert old_id in (new_meta.get("supersedes") or [])
    assert result["old_claim"]["claim_status"] == "stale"
    assert result["new_claim"]["supersedes"] == [old_id]


def test_supersede_is_ledgered_with_actor_basis_and_hashes(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    old_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    new_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))

    supersede_claim(vault, new_id, old_id, actor="cso", basis=["src-1", "review-7"])

    ledger = vault / ".constellation" / "supersedes-ledger.jsonl"
    assert ledger.is_file()
    import json
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["new_claim_id"] == new_id
    assert entry["old_claim_id"] == old_id
    assert entry["actor"] == "cso"
    assert entry["basis"] == ["src-1", "review-7"]
    assert entry["timestamp"].endswith("+00:00")
    assert len(entry["old_claim_hash"]) == 64  # rollback authority
    assert len(entry["new_claim_hash"]) == 64


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    old_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    new_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))

    supersede_claim(vault, new_id, old_id, actor="cso", basis=["src-1"])
    before_old = (vault / "claims" / f"{old_id}.md").read_bytes()
    before_new = (vault / "claims" / f"{new_id}.md").read_bytes()

    result = supersede_claim(vault, new_id, old_id, actor="cso", basis=["src-1"])

    assert result["status"] == "already_applied"
    assert (vault / "claims" / f"{old_id}.md").read_bytes() == before_old
    assert (vault / "claims" / f"{new_id}.md").read_bytes() == before_new
    ledger = vault / ".constellation" / "supersedes-ledger.jsonl"
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_already_stale_requires_force(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    older_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "100"))
    old_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    new_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))
    supersede_claim(vault, old_id, older_id, actor="cso", basis=["src-1"])

    with pytest.raises(SupersedesError, match="stale|force"):
        supersede_claim(vault, new_id, older_id, actor="cso", basis=["src-2"])


def test_force_on_stale_stages_review_candidate_instead_of_writing(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    older_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "100"))
    old_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    new_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))
    supersede_claim(vault, old_id, older_id, actor="cso", basis=["src-1"])
    before = (vault / "claims" / f"{older_id}.md").read_bytes()

    result = supersede_claim(vault, new_id, older_id, actor="cso", basis=["src-2"], force=True)

    assert result["status"] == "staged_review"
    assert result["candidate_id"]
    assert (vault / "claims" / f"{older_id}.md").read_bytes() == before  # no direct write
    assert (vault / ".constellation" / "candidates" / f"{result['candidate_id']}.json").is_file()


def test_missing_claim_fails_closed(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    new_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))

    with pytest.raises(SupersedesError, match="not found|missing"):
        supersede_claim(vault, new_id, generate_ulid(), actor="cso", basis=["src-1"])


def test_self_supersede_rejected(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    claim_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))

    with pytest.raises(SupersedesError, match="itself|self"):
        supersede_claim(vault, claim_id, claim_id, actor="cso", basis=["src-1"])


def test_empty_basis_rejected(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    old_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    new_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))

    with pytest.raises(SupersedesError, match="basis"):
        supersede_claim(vault, new_id, old_id, actor="cso", basis=[])


def test_chain_walks_three_links_cited(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    id1 = _write(vault, "claims", _claim(generate_ulid(), entity_id, "100"))
    id2 = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    id3 = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))
    supersede_claim(vault, id2, id1, actor="cso", basis=["src-1"])
    supersede_claim(vault, id3, id2, actor="cso", basis=["src-2"])

    # query from the MIDDLE of the chain — must resolve both directions
    chain = supersede_chain(vault, id2)

    assert [e["id"] for e in chain["entries"]] == [id1, id2, id3]
    assert chain["head"] == id3  # newest live claim
    assert chain["entries"][0]["claim_status"] == "stale"
    assert chain["entries"][1]["claim_status"] == "stale"
    assert chain["entries"][2]["claim_status"] != "stale"
    for entry in chain["entries"]:
        assert entry["path"] == f"claims/{entry['id']}.md"  # cited end to end
        assert entry["object_literal"] in {"100", "120", "135"}


def test_chain_of_live_claim_is_single_entry(tmp_path: Path) -> None:
    vault, entity_id = _vault_with_entity(tmp_path)
    claim_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))

    chain = supersede_chain(vault, claim_id)

    assert chain["head"] == claim_id
    assert [e["id"] for e in chain["entries"]] == [claim_id]


def test_chain_missing_claim_fails_closed(tmp_path: Path) -> None:
    vault, _ = _vault_with_entity(tmp_path)

    with pytest.raises(SupersedesError, match="not found|missing"):
        supersede_chain(vault, generate_ulid())


def test_stale_claim_still_validates(tmp_path: Path) -> None:
    from constellation.validation import validate_vault

    vault, entity_id = _vault_with_entity(tmp_path)
    old_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "120"))
    new_id = _write(vault, "claims", _claim(generate_ulid(), entity_id, "135"))
    supersede_claim(vault, new_id, old_id, actor="cso", basis=["src-1"])

    report = validate_vault(vault)
    assert report["invalid"] == 0, report.get("errors")
