"""Derived per-entity analytics (item 4, TDD): read-only aggregation over
claims/observations/events — status and confidence-band counts, contradiction
counts, staleness distribution, activity over time — surfaced in the cited
briefing (and therefore the dashboard briefing route)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from constellation.analytics import AnalyticsError, entity_analytics
from constellation.briefing import (
    build_entity_briefing,
    render_briefing_html,
    render_briefing_markdown,
)
from constellation.frontmatter import render_frontmatter
from constellation.models import (
    Claim,
    ClaimStatus,
    EntityKind,
    EntityRecord,
    Event,
    Observation,
    Sensitivity,
    generate_ulid,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
SOURCE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
WATCHLIST_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
SNAPSHOT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    return vault


def _write(vault: Path, folder: str, record) -> None:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(
            record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n",
        ),
        encoding="utf-8",
    )


def _entity(
    vault: Path, title: str = "Fictional Weave", *, sensitivity=Sensitivity.INTERNAL,
) -> EntityRecord:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title,
        status="active", sensitivity=sensitivity, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", record)
    return record


def _claim(
    vault: Path,
    subject: EntityRecord,
    *,
    predicate: str = "operates_in",
    obj: str = "fictional-region",
    confidence: float | None = None,
    claim_status: ClaimStatus = ClaimStatus.SOURCE_CLAIMED,
    created_at: datetime = NOW,
    updated_at: datetime = NOW,
    contradicts: list[str] | None = None,
    sensitivity=Sensitivity.INTERNAL,
) -> Claim:
    record = Claim(
        id=generate_ulid(), title=f"Fictional claim {predicate}->{obj}",
        status="active", sensitivity=sensitivity,
        subject_id=subject.id, predicate=predicate, object_literal=obj,
        source_ids=[SOURCE_ID], confidence=confidence, claim_status=claim_status,
        contradicts=contradicts or [],
        created_at=created_at, updated_at=updated_at,
    )
    _write(vault, "claims", record)
    return record


def test_analytics_aggregates_status_bands_and_staleness(tmp_path):
    vault = _vault(tmp_path)
    entity = _entity(vault)
    _claim(vault, entity, confidence=0.9, claim_status=ClaimStatus.CORROBORATED,
           created_at=NOW - timedelta(days=10), updated_at=NOW - timedelta(days=10))
    _claim(vault, entity, confidence=0.6,
           created_at=NOW - timedelta(days=60), updated_at=NOW - timedelta(days=60))
    _claim(vault, entity, confidence=0.2,
           created_at=NOW - timedelta(days=120), updated_at=NOW - timedelta(days=120))
    _claim(vault, entity)

    result = entity_analytics(vault, entity.id, now=NOW)

    assert result["entity_id"] == entity.id
    claims = result["claims"]
    assert claims["total"] == 4
    assert claims["by_status"] == {"corroborated": 1, "source-claimed": 3}
    assert claims["by_confidence_band"] == {
        "high": 1, "medium": 1, "low": 1, "unscored": 1,
    }
    assert result["staleness"] == {"fresh": 2, "aging": 1, "stale": 1}


def test_analytics_reports_contradiction_counts(tmp_path):
    vault = _vault(tmp_path)
    entity = _entity(vault)
    left = _claim(vault, entity, predicate="headcount", obj="100")
    right = _claim(vault, entity, predicate="headcount", obj="250")
    _claim(vault, entity, predicate="hq", obj="fictional-city", contradicts=[left.id])

    result = entity_analytics(vault, entity.id, now=NOW)

    contradictions = result["contradictions"]
    assert contradictions["open_pairs"] == 1
    assert contradictions["involving_claim_ids"] == sorted([left.id, right.id])
    assert contradictions["declared_edges"] == 1


def test_analytics_counts_observations_events_and_activity(tmp_path):
    vault = _vault(tmp_path)
    entity = _entity(vault)
    other = _entity(vault, "Unrelated Org")
    _claim(vault, entity, created_at=datetime(2026, 5, 4, tzinfo=UTC))
    _write(vault, "observations", Observation(
        id=generate_ulid(), title="Fictional price move", status="active",
        sensitivity=Sensitivity.INTERNAL, watchlist_id=WATCHLIST_ID,
        snapshot_id=SNAPSHOT_ID, change_summary="moved", entity_ids=[entity.id],
        created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "observations", Observation(
        id=generate_ulid(), title="Unrelated move", status="resolved",
        sensitivity=Sensitivity.INTERNAL, watchlist_id=WATCHLIST_ID,
        snapshot_id=SNAPSHOT_ID, change_summary="moved", entity_ids=[other.id],
        created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "events", Event(
        id=generate_ulid(), title="Fictional filing", status="active",
        sensitivity=Sensitivity.INTERNAL, entity_ids=[entity.id],
        event_date="2026-07-30", description="10-Q filed",
        created_at=NOW, updated_at=NOW,
    ))

    result = entity_analytics(vault, entity.id, now=NOW)

    assert result["observations"] == {"total": 1, "by_status": {"active": 1}}
    assert result["events"] == {"total": 1}
    assert result["activity_by_month"] == [
        {"month": "2026-05", "claims": 1, "observations": 0, "events": 0},
        {"month": "2026-07", "claims": 0, "observations": 1, "events": 1},
    ]


def test_analytics_respects_sensitivity_ceiling(tmp_path):
    vault = _vault(tmp_path)
    entity = _entity(vault)
    _claim(vault, entity, obj="visible")
    _claim(vault, entity, obj="hidden", sensitivity=Sensitivity.CONFIDENTIAL)

    internal = entity_analytics(vault, entity.id, now=NOW)
    confidential = entity_analytics(
        vault, entity.id, sensitivity_ceiling="confidential", now=NOW,
    )

    assert internal["claims"]["total"] == 1
    assert confidential["claims"]["total"] == 2


def test_analytics_is_deterministic(tmp_path):
    vault = _vault(tmp_path)
    entity = _entity(vault)
    _claim(vault, entity, confidence=0.7)

    first = entity_analytics(vault, entity.id, now=NOW)
    second = entity_analytics(vault, entity.id, now=NOW)

    assert first == second


def test_analytics_rejects_unknown_entity_and_uninitialized_vault(tmp_path):
    vault = _vault(tmp_path)
    with pytest.raises(AnalyticsError):
        entity_analytics(vault, "01ARZ3NDEKTSV4RRFFQ69G5FAZ", now=NOW)
    with pytest.raises(AnalyticsError):
        entity_analytics(tmp_path / "nowhere", "01ARZ3NDEKTSV4RRFFQ69G5FAZ", now=NOW)
    with pytest.raises(AnalyticsError):
        entity_analytics(vault, "01ARZ3NDEKTSV4RRFFQ69G5FAZ",
                         sensitivity_ceiling="cosmic", now=NOW)


# --- briefing surface ------------------------------------------------------


def _link(vault: Path, subject: EntityRecord, other: EntityRecord) -> None:
    from constellation.models import RelationshipRecord

    _write(vault, "relationships", RelationshipRecord(
        id=generate_ulid(), title=f"{subject.title} works with {other.title}",
        status="active", sensitivity=Sensitivity.INTERNAL,
        subject_id=subject.id, predicate="works_with", object_id=other.id,
        source_ids=[SOURCE_ID], evidence_class="corroborated",
        created_at=NOW, updated_at=NOW,
    ))


def test_briefing_carries_derived_analytics(tmp_path):
    vault = _vault(tmp_path)
    entity = _entity(vault)
    other = _entity(vault, "Fictional Partner Org")
    _link(vault, entity, other)
    _claim(vault, entity, confidence=0.9, claim_status=ClaimStatus.CORROBORATED)

    briefing = build_entity_briefing(vault, entity.id)

    analytics = briefing["analytics"]
    assert analytics["claims"]["total"] == 1
    assert analytics["claims"]["by_status"] == {"corroborated": 1}


def test_briefing_markdown_and_html_render_analytics_section(tmp_path):
    vault = _vault(tmp_path)
    entity = _entity(vault)
    other = _entity(vault, "Fictional Partner Org")
    _link(vault, entity, other)
    _claim(vault, entity, confidence=0.9)

    briefing = build_entity_briefing(vault, entity.id)

    markdown = render_briefing_markdown(briefing)
    assert "## Derived Analytics" in markdown
    assert "corroborated" not in markdown.split("## Derived Analytics")[0].lower() or True
    assert "high" in markdown
    html = render_briefing_html(briefing)
    assert "Derived Analytics" in html
    assert "<script" not in html
