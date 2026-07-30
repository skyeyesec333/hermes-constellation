"""Derived per-entity analytics — read-only aggregation, never mutates.

Rolls up the canonical record graph for one subject into the numbers an
operator actually asks for: claim counts by status and confidence band,
open contradiction pairs, observation/event counts, staleness distribution
of the claim base, and activity over time. Surfaced inside the cited
briefing (build_entity_briefing) and therefore on the dashboard briefing
route; every figure is derived from canonical records and reproducible —
the same vault and the same `now` always yield the same analytics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contradictions import detect_contradictions
from .frontmatter import parse_frontmatter
from .identity import resolve_subject
from .models import Claim, Event, Observation
from .vault import is_initialized


class AnalyticsError(RuntimeError):
    """Raised when analytics cannot be computed for the requested subject."""


_CEILINGS = ("public", "internal", "confidential", "restricted")

_FRESH_DAYS = 30
_AGING_DAYS = 90


def _confidence_band(confidence: float | None) -> str:
    if confidence is None:
        return "unscored"
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _staleness_bucket(updated_at: datetime, now: datetime) -> str:
    age_days = (now - updated_at).days
    if age_days <= _FRESH_DAYS:
        return "fresh"
    if age_days <= _AGING_DAYS:
        return "aging"
    return "stale"


def _within_ceiling(sensitivity: str, ceiling: str) -> bool:
    try:
        return _CEILINGS.index(sensitivity) <= _CEILINGS.index(ceiling)
    except ValueError:
        return False


def _scan_records(vault: Path, folder: str, model: type) -> list[Any]:
    base = vault / folder
    if not base.is_dir() or base.is_symlink():
        return []
    records: list[Any] = []
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
            records.append(model.model_validate(metadata, strict=False))
        except Exception:
            continue  # invalid records are validation's lane, not analytics'
    return records


def entity_analytics(
    root: Path | str,
    entity_id: str,
    *,
    sensitivity_ceiling: str = "internal",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate claims/observations/events for one entity. Read-only."""
    if sensitivity_ceiling not in _CEILINGS:
        raise AnalyticsError(f"unknown sensitivity ceiling: {sensitivity_ceiling}")
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise AnalyticsError("analytics requires an initialized vault")
    try:
        subject = resolve_subject(vault, entity_id)
    except Exception as exc:
        raise AnalyticsError(f"entity not found: {entity_id}") from exc

    moment = now or datetime.now(UTC)

    claims = [
        claim for claim in _scan_records(vault, "claims", Claim)
        if claim.subject_id == entity_id
        and _within_ceiling(claim.sensitivity.value, sensitivity_ceiling)
    ]
    observations = [
        obs for obs in _scan_records(vault, "observations", Observation)
        if entity_id in obs.entity_ids
        and _within_ceiling(obs.sensitivity.value, sensitivity_ceiling)
    ]
    events = [
        event for event in _scan_records(vault, "events", Event)
        if entity_id in event.entity_ids
        and _within_ceiling(event.sensitivity.value, sensitivity_ceiling)
    ]

    by_status: dict[str, int] = {}
    by_band = {"high": 0, "medium": 0, "low": 0, "unscored": 0}
    staleness = {"fresh": 0, "aging": 0, "stale": 0}
    for claim in claims:
        by_status[claim.claim_status.value] = by_status.get(claim.claim_status.value, 0) + 1
        by_band[_confidence_band(claim.confidence)] += 1
        staleness[_staleness_bucket(claim.updated_at, moment)] += 1

    in_ceiling_claim_ids = {claim.id for claim in claims}
    involving: set[str] = set()
    open_pairs = 0
    for proposal in detect_contradictions(vault):
        if proposal.get("subject_id") != entity_id:
            continue
        visible = {
            str(entry.get("id", "")) for entry in proposal.get("claims", [])
        } & in_ceiling_claim_ids
        if not visible:
            continue
        open_pairs += 1
        involving |= visible

    obs_by_status: dict[str, int] = {}
    for obs in observations:
        obs_by_status[obs.status] = obs_by_status.get(obs.status, 0) + 1

    activity: dict[str, dict[str, int]] = {}

    def _bump(when: datetime, kind: str) -> None:
        month = when.astimezone(UTC).strftime("%Y-%m")
        bucket = activity.setdefault(month, {"claims": 0, "observations": 0, "events": 0})
        bucket[kind] += 1

    for claim in claims:
        _bump(claim.created_at, "claims")
    for obs in observations:
        _bump(obs.created_at, "observations")
    for event in events:
        _bump(event.created_at, "events")

    return {
        "entity_id": entity_id,
        "entity_title": subject.record.title,
        "sensitivity_ceiling": sensitivity_ceiling,
        "as_of": moment.isoformat(),
        "claims": {
            "total": len(claims),
            "by_status": dict(sorted(by_status.items())),
            "by_confidence_band": by_band,
        },
        "contradictions": {
            "open_pairs": open_pairs,
            "involving_claim_ids": sorted(involving),
            "declared_edges": sum(1 for claim in claims if claim.contradicts),
        },
        "observations": {"total": len(observations), "by_status": dict(sorted(obs_by_status.items()))},
        "events": {"total": len(events)},
        "staleness": staleness,
        "activity_by_month": [
            {"month": month, **activity[month]} for month in sorted(activity)
        ],
    }
