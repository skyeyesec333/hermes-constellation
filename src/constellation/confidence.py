"""Stage 7.2 — confidence as a living, COMPUTED value.

score = base x decay(age) + reinforcement bonus, clamped to [0, 1].

- base: the claim's explicit ``confidence`` when set (human-approved),
  otherwise a band derived from ``claim_status``;
- decay: Ebbinghaus-style exponential (0.5 ** (age / half_life)). Stability
  class comes from the predicate: durable facts (founding, headquarters,
  architecture) decay slowly, transient facts (pricing, headcount) fast,
  everything else at the standard 90-day half-life;
- reinforcement: confirming evidence (``source_ids`` + ``supports`` links)
  adds a bounded bonus — corroboration strengthens, never replaces decay;
- stale/superseded claims are floored: preserved for history (7.1) but
  never outrank live evidence.

This module is PURE DERIVATION. It reads canonical metadata and returns a
recomputable score — it never writes, and callers must store the result
only as an index/display artifact, never back into the canonical record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .predicates import predicate_stability

_BASE_BY_STATUS = {
    "corroborated": 0.8,
    "source-claimed": 0.5,
    "inferred": 0.4,
    "disputed": 0.2,
    "stale": 0.1,
    "superseded": 0.1,
}
_TERMINAL_STATES = {"stale", "superseded"}

_HALF_LIFE_DAYS = {"durable": 365.0, "standard": 90.0, "transient": 14.0}

# Stability classes resolve through the shared predicate registry lookup
# (predicates.predicate_stability); the legacy claim-only vocabulary is
# preserved there so existing decay behavior does not shift.

_REINFORCEMENT_STEP = 0.05
_REINFORCEMENT_CAP = 6  # max 0.30 bonus


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def compute_confidence(
    metadata: dict[str, Any],
    *,
    now: datetime,
    confirmations: int | None = None,
) -> dict[str, Any]:
    """Compute the live confidence score for one claim's canonical metadata.

    ``now`` is injected for determinism; ``confirmations`` may be supplied
    by a caller that already counted corroboration, otherwise it is derived
    from source_ids + supports. Input is never mutated.
    """
    status = str(metadata.get("claim_status", "source-claimed"))
    explicit = metadata.get("confidence")
    base = float(explicit) if explicit is not None else _BASE_BY_STATUS.get(status, 0.5)

    predicate = str(metadata.get("predicate", ""))
    stability = predicate_stability(predicate)
    half_life = _HALF_LIFE_DAYS[stability]

    timestamp = _parse_time(metadata.get("observed_at")) or _parse_time(metadata.get("created_at"))
    reference = now if now.tzinfo else now.replace(tzinfo=UTC)
    age_days = max(0.0, (reference - timestamp).total_seconds() / 86400.0) if timestamp else 0.0
    decay = 0.5 ** (age_days / half_life)

    if confirmations is None:
        sources = metadata.get("source_ids") or []
        supports = metadata.get("supports") or []
        confirmations = len(sources) + len(supports)
    floored = status in _TERMINAL_STATES
    bonus = 0.0 if floored else _REINFORCEMENT_STEP * min(confirmations, _REINFORCEMENT_CAP)

    score = max(0.0, min(1.0, base * decay + bonus))
    return {
        "score": round(score, 6),
        "base": round(base, 6),
        "decay": round(decay, 6),
        "age_days": int(age_days),
        "stability": stability,
        "half_life_days": half_life,
        "confirmations": confirmations,
        "floored": floored,
        "derived": True,  # recomputable artifact — never a canonical write
    }
