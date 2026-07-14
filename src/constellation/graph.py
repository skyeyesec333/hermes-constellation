"""Bounded, read-only traversal of canonical relationship records."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .frontmatter import FrontmatterError, parse_frontmatter
from .models import RelationshipRecord
from .storage import safe_relative_path
from .vault import is_initialized


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


def _relationship_summary(record: RelationshipRecord) -> dict[str, object]:
    return {
        "relationship_id": record.id,
        "subject_id": record.subject_id,
        "predicate": record.predicate,
        "object_id": record.object_id,
        "source_ids": record.source_ids,
        "evidence_class": record.evidence_class,
        "sensitivity": record.sensitivity.value,
    }


def neighbors(root: Path | str, entity_id: str) -> dict[str, object]:
    """Return direct, evidence-backed relationships touching one entity."""
    results = []
    for record in _relationship_records(root):
        if entity_id not in {record.subject_id, record.object_id}:
            continue
        results.append(_relationship_summary(record))
    results.sort(key=lambda relationship: str(relationship["relationship_id"]))
    return {
        "status": "relationships_found" if results else "no_relationships_found",
        "relationships": results,
    }


def path(root: Path | str, start_entity_id: str, end_entity_id: str, *, max_hops: int = 4) -> dict[str, object]:
    """Return one deterministic shortest chain of sourced relationships."""
    if not 1 <= max_hops <= 4:
        raise GraphError("max_hops must be between 1 and 4")
    records = sorted(_relationship_records(root), key=lambda record: record.id)
    queue: list[tuple[str, list[RelationshipRecord], set[str]]] = [(start_entity_id, [], {start_entity_id})]
    while queue:
        current, chain, seen = queue.pop(0)
        if len(chain) >= max_hops:
            continue
        for record in records:
            if current == record.subject_id:
                next_entity = record.object_id
            elif current == record.object_id:
                next_entity = record.subject_id
            else:
                continue
            if next_entity in seen:
                continue
            next_chain = [*chain, record]
            if next_entity == end_entity_id:
                return {"status": "path_found", "path": [_relationship_summary(item) for item in next_chain]}
            queue.append((next_entity, next_chain, seen | {next_entity}))
    return {"status": "no_path_found", "path": []}
