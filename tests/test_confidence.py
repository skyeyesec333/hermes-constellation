"""Tests for Stage 7.2 confidence decay + reinforcement.

Contract: confidence is a COMPUTED, recomputable derivation —
base (explicit confidence or claim_status band) x Ebbinghaus decay(age)
+ reinforcement bonus (confirming sources). It never overwrites the
canonical record's stored value; the score is an index/display artifact.
"""

from datetime import UTC, datetime, timedelta

import pytest

from constellation.confidence import compute_confidence

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _meta(**overrides) -> dict:
    base = {
        "id": "01KYR000000000000000000000",
        "type": "claim",
        "claim_status": "source-claimed",
        "predicate": "headcount",
        "source_ids": ["01KYS000000000000000000000"],
        "supports": [],
        "created_at": NOW.isoformat(),
    }
    base.update(overrides)
    return base


def test_explicit_confidence_is_the_base() -> None:
    result = compute_confidence(_meta(confidence=0.9), now=NOW)
    assert result["base"] == 0.9
    assert result["score"] > 0.9  # fresh + one confirming source bonus


def test_status_bands_when_no_explicit_confidence() -> None:
    assert compute_confidence(_meta(claim_status="corroborated"), now=NOW)["base"] == 0.8
    assert compute_confidence(_meta(claim_status="source-claimed"), now=NOW)["base"] == 0.5
    assert compute_confidence(_meta(claim_status="inferred"), now=NOW)["base"] == 0.4
    assert compute_confidence(_meta(claim_status="disputed"), now=NOW)["base"] == 0.2


def test_ebbinghaus_decay_halves_at_half_life() -> None:
    fresh = compute_confidence(_meta(), now=NOW)
    aged = compute_confidence(
        _meta(predicate="partnered_with", created_at=(NOW - timedelta(days=90)).isoformat()), now=NOW
    )
    # standard stability: 90-day half-life -> decay factor ~0.5
    assert aged["stability"] == "standard"
    assert aged["decay"] == pytest.approx(0.5, abs=0.01)
    assert aged["score"] < fresh["score"]


def test_three_confirmations_outrank_unconfirmed_twin() -> None:
    confirmed = compute_confidence(
        _meta(source_ids=["s1", "s2", "s3"]), now=NOW
    )
    twin = compute_confidence(_meta(source_ids=["s1"]), now=NOW)
    assert confirmed["confirmations"] == 3
    assert confirmed["score"] > twin["score"]


def test_ninety_day_unreinforced_drops_below_fresh() -> None:
    old = compute_confidence(
        _meta(created_at=(NOW - timedelta(days=90)).isoformat(), source_ids=["s1"]),
        now=NOW,
    )
    fresh = compute_confidence(_meta(source_ids=["s1"]), now=NOW)
    assert old["score"] < fresh["score"]


def test_durable_predicate_decays_slower_than_transient() -> None:
    aged_durable = compute_confidence(
        _meta(predicate="founded_in", created_at=(NOW - timedelta(days=90)).isoformat()),
        now=NOW,
    )
    aged_transient = compute_confidence(
        _meta(predicate="pricing", created_at=(NOW - timedelta(days=90)).isoformat()),
        now=NOW,
    )
    assert aged_durable["stability"] == "durable"
    assert aged_transient["stability"] == "transient"
    assert aged_durable["score"] > aged_transient["score"]


def test_stale_claim_is_floored_below_live_twin() -> None:
    stale = compute_confidence(
        _meta(claim_status="stale", source_ids=["s1", "s2", "s3"]), now=NOW
    )
    live = compute_confidence(_meta(source_ids=["s1"]), now=NOW)
    assert stale["floored"] is True
    assert stale["score"] <= 0.1
    assert stale["score"] < live["score"]


def test_supports_links_count_as_confirmations() -> None:
    result = compute_confidence(
        _meta(source_ids=["s1"], supports=["c1", "c2"]), now=NOW
    )
    assert result["confirmations"] == 3


def test_score_is_clamped_and_deterministic() -> None:
    meta = _meta(confidence=1.0, source_ids=[f"s{i}" for i in range(10)])
    first = compute_confidence(meta, now=NOW)
    second = compute_confidence(meta, now=NOW)
    assert 0.0 <= first["score"] <= 1.0
    assert first == second


def test_input_metadata_is_never_mutated() -> None:
    meta = _meta()
    snapshot = dict(meta)
    compute_confidence(meta, now=NOW)
    assert meta == snapshot


def test_observed_at_takes_priority_over_created_at() -> None:
    result = compute_confidence(
        _meta(
            created_at=(NOW - timedelta(days=200)).isoformat(),
            observed_at=NOW.isoformat(),
        ),
        now=NOW,
    )
    assert result["age_days"] == 0


# ── integration: retrieval ranking + briefing display ─────────────────────

import hashlib  # noqa: E402
from pathlib import Path  # noqa: E402

from constellation.briefing import build_entity_briefing  # noqa: E402
from constellation.frontmatter import render_frontmatter  # noqa: E402
from constellation.models import (  # noqa: E402
    Claim,
    EntityKind,
    EntityRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.retrieval import build_index, search  # noqa: E402
from constellation.vault import initialize_vault  # noqa: E402


def _write_record(vault: Path, folder: str, record) -> None:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n"),
        encoding="utf-8",
    )


def _claims_vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="ConfCo",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write_record(vault, "entities", entity)
    source = SourceItem(
        id=generate_ulid(), type="source_item", title="src",
        status="active", sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"bytes").hexdigest(),
        original_path="Library/Files/s.pdf", media_type="application/pdf",
        created_at=NOW, updated_at=NOW,
    )
    _write_record(vault, "source-items", source)
    claim = Claim(
        id=generate_ulid(), title="ConfCo expanding", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity.id,
        predicate="expanding_into", object_literal="Thailand",
        source_ids=[source.id], created_at=NOW, updated_at=NOW,
    )
    (vault / "claims" / f"{claim.id}.md").write_text(
        render_frontmatter(claim.model_dump(mode="json", exclude_none=True),
                           "# ConfCo expanding\n\nThailand\n"),
        encoding="utf-8",
    )
    return vault, entity.id


def test_briefing_claims_carry_derived_confidence(tmp_path: Path) -> None:
    vault, entity_id = _claims_vault(tmp_path)

    model = build_entity_briefing(vault, entity_id)

    assert model["claims"], "fixture claim should surface in the briefing"
    entry = model["claims"][0]
    assert "confidence_score" in entry
    assert entry["confidence_score"]["derived"] is True
    assert 0.0 < entry["confidence_score"]["score"] <= 1.0


def test_search_claim_results_carry_derived_confidence(tmp_path: Path) -> None:
    vault, _ = _claims_vault(tmp_path)
    build_index(vault)

    packet = search(vault, "Thailand", sensitivity_ceiling="internal")

    assert packet["status"] == "evidence_found"
    claim_rows = [e for e in packet["evidence"] if str(e["path"]).startswith("claims/")]
    assert claim_rows, "claim should be retrievable"
    assert claim_rows[0]["confidence"]["derived"] is True
    assert claim_rows[0]["confidence"]["score"] > 0.0
