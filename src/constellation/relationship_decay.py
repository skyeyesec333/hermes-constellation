"""Relationship decay: reports and review-gated suggestions (Wave 6 Task 6.3).

Confidence decay uses the shared stability-class half-lives (durable 365d,
standard 90d, transient 14d) measured from the relationship's freshest
evidence timestamp (last_seen, falling back to observed_at/updated_at).
Relationships past their valid_to are flagged expired.

Two modes, both conservative:

- ``decay_report`` — read-only report with temporal states, suggested
  confidence, reasons, and counts. Auto-applies nothing.
- ``stage_decay_suggestions`` — stages review-only candidate patches
  (expired → status stale; decayed → suggested confidence) through the
  existing review machinery. Bounded, idempotent, never a canonical write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .confidence import _HALF_LIFE_DAYS
from .frontmatter import parse_frontmatter, render_frontmatter
from .predicates import default_registry, predicate_stability
from .relationship_backfill import _pending_patch_targets
from .review import write_candidate
from .storage import safe_relative_path, sha256_file
from .vault import is_initialized

_SUGGESTION_CONFIDENCE_DROP = 0.1


class RelationshipDecayError(RuntimeError):
    """Raised when decay operations fail closed."""


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _scan_relationships(vault: Path) -> list[tuple[Path, dict[str, Any]]]:
    base = vault / "relationships"
    found: list[tuple[Path, dict[str, Any]]] = []
    if not base.is_dir():
        return found
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not metadata.get("id"):
            continue
        if str(metadata.get("status", "")) in {"stale", "superseded"}:
            continue
        found.append((path, metadata))
    return found


def _evaluate(
    metadata: dict[str, Any], registry, *, as_of: datetime
) -> dict[str, Any]:
    predicate = str(metadata.get("predicate", ""))
    stability = predicate_stability(predicate, registry)
    half_life = _HALF_LIFE_DAYS[stability]
    valid_to = _parse_time(metadata.get("valid_to"))
    valid_from = _parse_time(metadata.get("valid_from"))
    if valid_to is not None and valid_to < as_of:
        temporal_state = "expired"
    elif valid_from is None and valid_to is None:
        temporal_state = "undated"
    else:
        temporal_state = "active"

    freshest = None
    for key in ("last_seen", "observed_at", "updated_at"):
        candidate = _parse_time(metadata.get(key))
        if candidate is not None and (freshest is None or candidate > freshest):
            freshest = candidate
    age_days = max((as_of - freshest).days, 0) if freshest else None
    confidence = metadata.get("confidence")
    suggested_confidence = None
    if confidence is not None and age_days is not None:
        suggested = float(confidence) * (0.5 ** (age_days / half_life))
        suggested_confidence = round(suggested, 4)

    reasons: list[str] = []
    if temporal_state == "expired":
        reasons.append("valid_to elapsed")
    if (
        confidence is not None
        and suggested_confidence is not None
        and float(confidence) - suggested_confidence >= _SUGGESTION_CONFIDENCE_DROP
    ):
        reasons.append(f"confidence decayed >= {_SUGGESTION_CONFIDENCE_DROP} ({stability} class)")
    return {
        "relationship_id": str(metadata["id"]),
        "predicate": predicate,
        "stability_class": stability,
        "temporal_state": temporal_state,
        "confidence": confidence,
        "suggested_confidence": suggested_confidence,
        "age_days": age_days,
        "reasons": reasons,
        "suggestion": bool(reasons),
    }


def decay_report(
    root: Path | str, *, as_of: datetime | None = None, manual: bool = True
) -> dict[str, Any]:
    """Read-only decay report. Auto-applies nothing."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise RelationshipDecayError("vault is not initialized")
    moment = as_of or datetime.now(UTC)
    if moment.tzinfo is None:
        raise RelationshipDecayError("as_of must be timezone-aware")
    registry = default_registry()
    entries = [
        _evaluate(metadata, registry, as_of=moment)
        for _, metadata in _scan_relationships(vault)
    ]
    entries.sort(key=lambda e: e["relationship_id"])
    counts = {
        "total": len(entries),
        "expired": sum(1 for e in entries if e["temporal_state"] == "expired"),
        "undated": sum(1 for e in entries if e["temporal_state"] == "undated"),
        "suggestions": sum(1 for e in entries if e["suggestion"]),
        "unscored": sum(1 for e in entries if e["confidence"] is None),
    }
    return {
        "status": "ok",
        "mode": "manual" if manual else "scheduled",
        "as_of": moment.isoformat(),
        "counts": counts,
        "entries": entries,
    }


def stage_decay_suggestions(
    root: Path | str, *, as_of: datetime | None = None, limit: int = 25
) -> dict[str, Any]:
    """Stage review-only candidate patches for decay suggestions. Bounded,
    idempotent, never a canonical write."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise RelationshipDecayError("vault is not initialized")
    if not 1 <= limit <= 200:
        raise RelationshipDecayError("limit must be between 1 and 200")
    moment = as_of or datetime.now(UTC)
    if moment.tzinfo is None:
        raise RelationshipDecayError("as_of must be timezone-aware")
    registry = default_registry()
    pending = _pending_patch_targets(vault)
    staged = 0
    already_pending = 0
    processed = 0
    for path, metadata in _scan_relationships(vault):
        evaluation = _evaluate(metadata, registry, as_of=moment)
        if not evaluation["suggestion"]:
            continue
        if processed >= limit:
            break
        processed += 1
        relationship_id = evaluation["relationship_id"]
        target_rel = f"relationships/{relationship_id}.md"
        if target_rel in pending:
            already_pending += 1
            continue
        target = safe_relative_path(vault, target_rel)
        if not target.is_file() or target.is_symlink():
            continue
        current, body = parse_frontmatter(target.read_text(encoding="utf-8"))
        updated = dict(current)
        changes: list[str] = []
        if evaluation["temporal_state"] == "expired":
            updated["status"] = "stale"
            changes.append("status=stale (valid_to elapsed)")
        if (
            evaluation["suggested_confidence"] is not None
            and evaluation["confidence"] is not None
            and float(evaluation["confidence"]) - float(evaluation["suggested_confidence"])
            >= _SUGGESTION_CONFIDENCE_DROP
        ):
            updated["confidence"] = evaluation["suggested_confidence"]
            changes.append(
                f"confidence {evaluation['confidence']}→{evaluation['suggested_confidence']}"
            )
        updated["updated_at"] = datetime.now(UTC).isoformat()
        from .models import CandidatePatch, Sensitivity

        candidate = CandidatePatch(
            type="candidate_patch",
            title=f"Decay suggestion for {relationship_id}: {'; '.join(changes)}",
            status="review-required",
            sensitivity=Sensitivity(str(current.get("sensitivity", "internal"))),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            target_path=target_rel,
            content=render_frontmatter(updated, body),
            expected_base_hash=sha256_file(target),
        )
        write_candidate(vault, candidate)
        pending.add(target_rel)
        staged += 1
    return {
        "status": "ok",
        "as_of": moment.isoformat(),
        "processed": processed,
        "staged": staged,
        "already_pending": already_pending,
        "limit": limit,
    }
