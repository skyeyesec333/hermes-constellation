"""Bounded, read-only traversal of canonical relationship records."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .frontmatter import FrontmatterError, parse_frontmatter
from .models import RelationshipRecord
from .storage import safe_relative_path
from .vault import is_initialized

_DIRECTIONS = {"both", "outgoing", "incoming", "directed"}


class GraphError(RuntimeError):
    """Raised when canonical relationship records cannot safely be read."""


def _relationship_records(root: Path | str) -> list[RelationshipRecord]:
    if not is_initialized(root):
        raise GraphError("graph queries require an initialized vault")
    directory = safe_relative_path(root, "relationships")
    records: list[RelationshipRecord] = []
    for path in sorted(directory.rglob("*.md")):
        if path.is_symlink() or not path.is_file():
            raise GraphError("relationship record is unsafe")
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            records.append(RelationshipRecord.model_validate(metadata, strict=False))
        except (FrontmatterError, OSError, ValidationError) as exc:
            raise GraphError("relationship record is invalid") from exc
    return records


def _validate_direction(direction: str) -> None:
    if direction not in _DIRECTIONS:
        raise GraphError(f"unknown direction: {direction}")


def _validate_as_of(as_of: datetime | None) -> None:
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise GraphError("as_of must include a timezone")


def _passes_filters(
    record: RelationshipRecord,
    *,
    predicates: set[str] | None,
    as_of: datetime | None,
    min_confidence: float | None,
) -> bool:
    if predicates is not None and record.predicate not in predicates:
        return False
    if min_confidence is not None and (
        record.confidence is None or record.confidence < min_confidence
    ):
        return False
    if as_of is not None:
        if record.valid_from is not None and record.valid_from > as_of:
            return False
        if record.valid_to is not None and record.valid_to < as_of:
            return False
    return True


def _temporal_status(record: RelationshipRecord, as_of: datetime | None) -> str:
    if record.valid_from is None and record.valid_to is None:
        return "unknown"
    if as_of is not None:
        return "active"
    return "dated"


def _relationship_summary(record: RelationshipRecord, as_of: datetime | None = None) -> dict[str, object]:
    return {
        "relationship_id": record.id,
        "subject_id": record.subject_id,
        "predicate": record.predicate,
        "object_id": record.object_id,
        "source_ids": record.source_ids,
        "evidence_class": record.evidence_class,
        "sensitivity": record.sensitivity.value,
        "confidence": record.confidence,
        "observed_at": record.observed_at.isoformat() if record.observed_at else None,
        "valid_from": record.valid_from.isoformat() if record.valid_from else None,
        "valid_to": record.valid_to.isoformat() if record.valid_to else None,
        "temporal_status": _temporal_status(record, as_of),
    }


def neighbors(
    root: Path | str,
    entity_id: str,
    *,
    predicates: set[str] | None = None,
    direction: str = "both",
    as_of: datetime | None = None,
    min_confidence: float | None = None,
) -> dict[str, object]:
    """Return direct, evidence-backed relationships touching one entity.

    Filters preserve defaults: with no options the behavior is identical to
    the pre-filter API. ``directed`` follows subject→object orientation
    (equivalent to ``outgoing`` for a neighbors query).
    """
    _validate_direction(direction)
    _validate_as_of(as_of)
    results = []
    excluded_by_as_of = 0
    for record in _relationship_records(root):
        if entity_id not in {record.subject_id, record.object_id}:
            continue
        if direction in {"outgoing", "directed"} and record.subject_id != entity_id:
            continue
        if direction == "incoming" and record.object_id != entity_id:
            continue
        if not _passes_filters(
            record, predicates=predicates, as_of=as_of, min_confidence=min_confidence
        ):
            if as_of is not None and (
                (record.valid_from is not None and record.valid_from > as_of)
                or (record.valid_to is not None and record.valid_to < as_of)
            ):
                excluded_by_as_of += 1
            continue
        results.append(_relationship_summary(record, as_of))
    results.sort(key=lambda relationship: str(relationship["relationship_id"]))
    result: dict[str, object] = {
        "status": "relationships_found" if results else "no_relationships_found",
        "relationships": results,
        "filters": {
            "predicates": sorted(predicates) if predicates else None,
            "direction": direction,
            "as_of": as_of.isoformat() if as_of else None,
            "min_confidence": min_confidence,
        },
    }
    if as_of is not None:
        result["excluded_by_as_of"] = excluded_by_as_of
    return result


def path(
    root: Path | str,
    start_entity_id: str,
    end_entity_id: str,
    *,
    max_hops: int = 4,
    predicates: set[str] | None = None,
    direction: str = "both",
    as_of: datetime | None = None,
    min_confidence: float | None = None,
) -> dict[str, object]:
    """Return one deterministic shortest chain of sourced relationships.

    ``direction`` controls traversal: ``both`` (default) walks either side of
    an edge; ``directed``/``outgoing`` follow subject→object only;
    ``incoming`` follows object→subject only.
    """
    if not 1 <= max_hops <= 4:
        raise GraphError("max_hops must be between 1 and 4")
    _validate_direction(direction)
    _validate_as_of(as_of)
    records = []
    excluded_by_as_of = 0
    for record in sorted(_relationship_records(root), key=lambda item: item.id):
        if not _passes_filters(
            record, predicates=predicates, as_of=as_of, min_confidence=min_confidence
        ):
            if as_of is not None and (
                (record.valid_from is not None and record.valid_from > as_of)
                or (record.valid_to is not None and record.valid_to < as_of)
            ):
                excluded_by_as_of += 1
            continue
        records.append(record)

    def _next_hops(current: str) -> list[tuple[str, RelationshipRecord]]:
        hops: list[tuple[str, RelationshipRecord]] = []
        for record in records:
            if direction in {"directed", "outgoing"}:
                if current == record.subject_id:
                    hops.append((record.object_id, record))
            elif direction == "incoming":
                if current == record.object_id:
                    hops.append((record.subject_id, record))
            else:
                if current == record.subject_id:
                    hops.append((record.object_id, record))
                elif current == record.object_id:
                    hops.append((record.subject_id, record))
        return hops

    queue: list[tuple[str, list[RelationshipRecord], set[str]]] = [(start_entity_id, [], {start_entity_id})]
    while queue:
        current, chain, seen = queue.pop(0)
        if len(chain) >= max_hops:
            continue
        for next_entity, record in _next_hops(current):
            if next_entity in seen:
                continue
            next_chain = [*chain, record]
            if next_entity == end_entity_id:
                result: dict[str, object] = {
                    "status": "path_found",
                    "path": [_relationship_summary(item, as_of) for item in next_chain],
                }
                if as_of is not None:
                    result["excluded_by_as_of"] = excluded_by_as_of
                return result
            queue.append((next_entity, next_chain, seen | {next_entity}))
    result = {"status": "no_path_found", "path": []}
    if as_of is not None:
        result["excluded_by_as_of"] = excluded_by_as_of
    return result
