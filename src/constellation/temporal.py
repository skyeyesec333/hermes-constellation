"""Temporal / as-of retrieval over canonical records.

Entity timelines are read-only projections: every entry cites its canonical
path, timestamps are explicit, and as-of boundaries are reported — never
silently applied. Sensitivity ceilings and evidence-class filtering use the
same rank semantics as search and book intelligence.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .vault import is_initialized

_SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

# folder -> (record type, time field preference, entity reference fields)
_TIMELINE_SOURCES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("claims", "claim", ("observed_at", "created_at"), ("subject_id", "object_id")),
    ("events", "event", ("event_date", "created_at"), ("entity_ids",)),
    ("observations", "observation", ("created_at",), ("entity_ids",)),
    ("decisions", "decision", ("decided_at", "created_at"), ("subject_id",)),
    ("opportunities", "opportunity", ("created_at",), ("subject_ids",)),
)


class TemporalError(RuntimeError):
    """Raised when temporal retrieval fails."""


def _parse_as_of(as_of: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(as_of)
    except ValueError as exc:
        raise TemporalError(f"as-of must be ISO-8601: {as_of}") from exc
    if parsed.tzinfo is None:
        raise TemporalError("as-of must include an explicit timezone")
    return parsed.astimezone(UTC)


def _record_time(metadata: dict[str, Any], time_fields: tuple[str, ...]) -> datetime | None:
    for field in time_fields:
        value = metadata.get(field)
        if not value:
            continue
        text = str(value)
        try:
            if field == "event_date" or (len(text) == 10 and text[4] == "-"):
                parsed_date = date.fromisoformat(text[:10])
                return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC)
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            continue
    return None


def _references_entity(metadata: dict[str, Any], ref_fields: tuple[str, ...], entity_id: str) -> bool:
    for field in ref_fields:
        value = metadata.get(field)
        if isinstance(value, list) and entity_id in {str(item) for item in value}:
            return True
        if isinstance(value, str) and value == entity_id:
            return True
    return False


def entity_timeline(
    vault: Path | str,
    entity_id: str,
    *,
    as_of: str | None = None,
    sensitivity_ceiling: str = "internal",
) -> dict[str, Any]:
    """Return the cited, time-ordered timeline of records referencing an entity.

    - ``as_of`` (optional ISO-8601 with timezone) excludes later records;
      ``truncated_by_as_of`` is True when referencing records exist beyond
      the boundary, so incomplete history is always explicit.
    - ``sensitivity_ceiling`` excludes higher-sensitivity records, counted in
      ``excluded_by_sensitivity``.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise TemporalError("vault is not initialized")

    ceiling = _SENSITIVITY_RANK.get(sensitivity_ceiling)
    if ceiling is None:
        raise TemporalError(f"unknown sensitivity ceiling: {sensitivity_ceiling}")

    boundary = _parse_as_of(as_of) if as_of else None

    entries: list[dict[str, Any]] = []
    excluded_by_sensitivity = 0
    excluded_by_as_of = 0

    for folder, record_type, time_fields, ref_fields in _TIMELINE_SOURCES:
        base = vault / folder
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                metadata, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not _references_entity(metadata, ref_fields, entity_id):
                continue

            rank = _SENSITIVITY_RANK.get(str(metadata.get("sensitivity", "internal")), 1)
            if rank > ceiling:
                excluded_by_sensitivity += 1
                continue

            timestamp = _record_time(metadata, time_fields)
            if timestamp is None:
                continue
            if boundary is not None and timestamp > boundary:
                excluded_by_as_of += 1
                continue

            entry: dict[str, Any] = {
                "id": str(metadata.get("id", "")),
                "type": record_type,
                "title": str(metadata.get("title", "")),
                "path": f"{folder}/{path.name}",
                "timestamp": timestamp.isoformat(),
            }
            if record_type == "event" and metadata.get("observation_ids"):
                raw_ids = metadata.get("observation_ids")
                entry["observation_ids"] = (
                    [str(item) for item in raw_ids] if isinstance(raw_ids, list) else []
                )
            entries.append(entry)

    entries.sort(key=lambda item: (item["timestamp"], item["type"], item["id"]))

    return {
        "entity_id": entity_id,
        "as_of": boundary.isoformat() if boundary else None,
        "entries": entries,
        "total_entries": len(entries),
        "truncated_by_as_of": excluded_by_as_of > 0,
        "excluded_by_as_of": excluded_by_as_of,
        "excluded_by_sensitivity": excluded_by_sensitivity,
        "sensitivity_ceiling": sensitivity_ceiling,
    }
