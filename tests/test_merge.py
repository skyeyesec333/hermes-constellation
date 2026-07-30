"""Tests for Stage 7.7 multi-writer merge semantics.

Contract: every canonical mutation through apply_record_update is a
per-field compare-and-swap over expected-hash atomic writes. Concurrent
non-conflicting writes (different fields) merge cleanly; a genuine conflict
(same field, divergent current value) stages a review candidate instead of
writing — there is no silent last-write-wins. Every applied merge is
journaled with actor + field-level detail.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.merge import MergeConflict, apply_record_update
from constellation.models import (
    EntityKind,
    EntityRecord,
    Sensitivity,
    generate_ulid,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _vault_with_entity(tmp_path: Path) -> tuple[Path, str, Path]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="MergeCo",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    path = vault / "entities" / f"{record.id}.md"
    path.write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), "# MergeCo\n"),
        encoding="utf-8",
    )
    return vault, record.id, path


def _meta(path: Path) -> dict:
    metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return metadata


def test_two_writers_different_fields_merge_cleanly(tmp_path: Path) -> None:
    vault, record_id, path = _vault_with_entity(tmp_path)

    first = apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"confidence": ("absent", 0.7)},
        actor="writer-a",
    )
    second = apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"aliases": ("absent", ["MergeCo Intl"])},
        actor="writer-b",
    )

    assert first["status"] == "applied"
    assert second["status"] == "applied"
    meta = _meta(path)
    assert meta["confidence"] == 0.7  # writer A's field survives
    assert meta["aliases"] == ["MergeCo Intl"]  # writer B's field lands


def test_genuine_conflict_stages_candidate_no_last_write_wins(tmp_path: Path) -> None:
    vault, record_id, path = _vault_with_entity(tmp_path)
    apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"confidence": ("absent", 0.7)},
        actor="writer-a",
    )
    before = path.read_bytes()

    result = apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"confidence": ("absent", 0.3)},  # stale expectation
        actor="writer-b",
    )

    assert result["status"] == "conflict_staged"
    assert result["conflicts"] == ["confidence"]
    assert path.read_bytes() == before  # NO silent last-write-wins
    candidate = vault / ".constellation" / "candidates" / f"{result['candidate_id']}.json"
    assert candidate.is_file()
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    assert payload["expected_base_hash"] is not None  # review sees exact base


def test_matching_expectation_applies(tmp_path: Path) -> None:
    vault, record_id, path = _vault_with_entity(tmp_path)
    apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"confidence": ("absent", 0.7)},
        actor="writer-a",
    )

    result = apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"confidence": (0.7, 0.9)},
        actor="writer-b",
    )

    assert result["status"] == "applied"
    assert _meta(path)["confidence"] == 0.9


def test_idempotent_same_value_is_noop(tmp_path: Path) -> None:
    vault, record_id, path = _vault_with_entity(tmp_path)
    apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"confidence": ("absent", 0.7)},
        actor="writer-a",
    )
    before = path.read_bytes()

    result = apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"confidence": ("absent", 0.7)},
        actor="writer-a",
    )

    assert result["status"] == "noop"
    assert path.read_bytes() == before


def test_applied_merge_is_journaled(tmp_path: Path) -> None:
    vault, record_id, path = _vault_with_entity(tmp_path)

    apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"confidence": ("absent", 0.7)},
        actor="writer-a",
    )

    journal = vault / ".constellation" / "merge-journal.jsonl"
    entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["actor"] == "writer-a"
    assert entry["path"] == f"entities/{record_id}.md"
    assert entry["fields"] == ["confidence"]
    assert len(entry["old_sha256"]) == 64
    assert len(entry["new_sha256"]) == 64


def test_missing_record_fails_closed(tmp_path: Path) -> None:
    vault, _, _ = _vault_with_entity(tmp_path)

    with pytest.raises(MergeConflict, match="not found|missing"):
        apply_record_update(
            vault, "entities/01NONEXISTENT00000000000.md",
            updates={"x": ("absent", "y")},
            actor="writer-a",
        )


def test_conflict_preflights_all_fields_before_any_write(tmp_path: Path) -> None:
    vault, record_id, path = _vault_with_entity(tmp_path)
    apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={"confidence": ("absent", 0.7)},
        actor="writer-a",
    )
    before = path.read_bytes()

    result = apply_record_update(
        vault, f"entities/{record_id}.md",
        updates={
            "aliases": ("absent", ["MergeCo Intl"]),  # would apply cleanly
            "confidence": ("absent", 0.3),               # conflicts
        },
        actor="writer-b",
    )

    assert result["status"] == "conflict_staged"
    assert path.read_bytes() == before  # even the clean field was NOT written
