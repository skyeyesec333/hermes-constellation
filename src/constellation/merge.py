"""Stage 7.7 — multi-writer merge semantics for canonical records.

Generalizes the CRM pattern to ALL canonical mutations through one API:
per-field compare-and-swap over expected-hash atomic writes.

- updates map each field to (expected, new): the write lands only when the
  CURRENT value matches expected (the literal string "absent" means the
  field must not exist). Concurrent writers touching DIFFERENT fields merge
  cleanly — later reads see both.
- A field whose current value diverges from the writer's expectation is a
  GENUINE CONFLICT: nothing is written (even fields that would apply
  cleanly — preflight is all-or-nothing) and a review candidate carrying
  the fully merged proposal + exact base hash is staged instead. There is
  no silent last-write-wins.
- A fully redundant update (every field already holds the new value) is a
  truthful noop — no write, no journal noise.
- Every applied merge is journaled (.constellation/merge-journal.jsonl)
  with actor, fields, and pre/post hashes.

Timestamp resolution for concurrent non-conflicting writes falls out of the
compare-and-swap: non-overlapping fields never conflict, so ordering
between writers is immaterial; updated_at is bumped on every applied merge.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter, render_frontmatter
from .models import CandidatePatch, Sensitivity, generate_ulid
from .review import write_candidate
from .storage import atomic_write_text, sha256_file
from .vault import is_initialized

ABSENT = "absent"
_JOURNAL_REL = Path(".constellation/merge-journal.jsonl")


class MergeConflict(RuntimeError):
    """Raised when a merge cannot proceed truthfully."""


def _is_absent(value: Any) -> bool:
    return value is None or value == [] or value == ""


def apply_record_update(
    vault: Path | str,
    record_path: str,
    *,
    updates: dict[str, tuple[Any, Any]],
    actor: str,
) -> dict[str, Any]:
    """Apply per-field compare-and-swap updates to one canonical record."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise MergeConflict("vault is not initialized")
    if not actor.strip():
        raise MergeConflict("actor is required")
    if not updates:
        raise MergeConflict("updates must not be empty")

    path = vault / record_path
    if path.is_symlink() or not path.is_file():
        raise MergeConflict(f"record not found: {record_path}")
    metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    old_sha = sha256_file(path)

    conflicts: list[str] = []
    changed: list[str] = []
    merged = dict(metadata)
    for field, (expected, new) in updates.items():
        current = metadata.get(field)
        if current == new:
            continue  # already in the desired state — truthful noop
        expected_missing = expected is None or expected == ABSENT
        if expected_missing and not _is_absent(current):
            conflicts.append(field)
            continue
        if not expected_missing and current != expected:
            conflicts.append(field)
            continue
        changed.append(field)
        merged[field] = new

    if conflicts:
        merged["updated_at"] = datetime.now(UTC).isoformat()
        candidate = CandidatePatch(
            id=generate_ulid(),
            type="candidate-patch",
            title=f"Merge conflict on {record_path} ({', '.join(sorted(conflicts))})",
            status="pending-review",
            sensitivity=Sensitivity(str(metadata.get("sensitivity", "internal"))),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            target_path=record_path,
            content=render_frontmatter(merged, body),
            expected_base_hash=old_sha,
        )
        candidate_path = write_candidate(vault, candidate)
        return {
            "status": "conflict_staged",
            "conflicts": sorted(conflicts),
            "candidate_id": candidate.id,
            "candidate_path": candidate_path.as_posix(),
        }

    if not changed:
        return {"status": "noop", "path": record_path, "fields": []}

    merged["updated_at"] = datetime.now(UTC).isoformat()
    content = render_frontmatter(merged, body)
    from .validation import CanonicalValidationError, validate_canonical_text

    try:
        validate_canonical_text(content, record_path)
    except CanonicalValidationError as exc:
        raise MergeConflict(
            f"merged record would fail canonical validation: {exc}"
        ) from exc
    atomic_write_text(vault, Path(record_path), content, expected_hash=old_sha)
    new_sha = sha256_file(path)

    journal = vault / _JOURNAL_REL
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "path": record_path,
            "fields": sorted(changed),
            "old_sha256": old_sha,
            "new_sha256": new_sha,
        }, sort_keys=True) + "\n")

    return {
        "status": "applied",
        "path": record_path,
        "fields": sorted(changed),
        "new_sha256": new_sha,
    }
