"""Tests for Stage 7.5 self-healing lint --fix.

Contract: --fix repairs ONLY mechanical issues — an orphan wikilink whose
link text exactly matches exactly ONE canonical record title is retargeted
to that record. Ambiguous or unmatched links, and every non-mechanical
finding, stay report-only. Every fix is journaled with pre/post hashes so
rollback replay restores original bytes exactly.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.models import (
    EntityKind,
    EntityRecord,
    Sensitivity,
    generate_ulid,
)
from constellation.record_lint import (
    RecordLintError,
    lint_fix,
    lint_records,
    rollback_lint_fix,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _entity(vault: Path, title: str) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title,
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    (vault / "entities" / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {title}\n"),
        encoding="utf-8",
    )
    return record.id


def _note(vault: Path, folder: str, stem: str, body: str) -> Path:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=stem,
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    path = vault / folder / f"{record.id}.md"
    path.write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), body),
        encoding="utf-8",
    )
    return path


def test_orphan_link_with_single_match_is_fixed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _entity(vault, "Nestle Thailand")
    note = _note(vault, "entities", "note-a", "# note\n\nSee [[Nestle Thailand]] for context.\n")

    result = lint_fix(vault, apply=True)

    assert len(result["fixes"]) == 1
    fix = result["fixes"][0]
    assert fix["link"] == "Nestle Thailand"
    assert fix["replacement"].startswith("entities/")
    body = note.read_text(encoding="utf-8")
    assert "[[Nestle Thailand]]" not in body
    assert "[[entities/" in body and "Nestle Thailand" in body
    assert result["remaining"] == []


def test_ambiguous_link_stays_report_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _entity(vault, "Acme")
    _entity(vault, "Acme")  # second record with the SAME title -> ambiguous
    note = _note(vault, "entities", "note-b", "# note\n\nSee [[Acme]] here.\n")
    before = note.read_bytes()

    result = lint_fix(vault, apply=True)

    assert result["fixes"] == []
    assert any(f["check"] == "orphan_wikilink" for f in result["remaining"])
    assert note.read_bytes() == before


def test_unmatched_link_stays_report_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _entity(vault, "Existing Corp")
    note = _note(vault, "entities", "note-c", "# note\n\nSee [[Nonexistent Thing]] here.\n")
    before = note.read_bytes()

    result = lint_fix(vault, apply=True)

    assert result["fixes"] == []
    assert any(f["check"] == "orphan_wikilink" for f in result["remaining"])
    assert note.read_bytes() == before


def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _entity(vault, "Nestle Thailand")
    note = _note(vault, "entities", "note-d", "# note\n\nSee [[Nestle Thailand]] for context.\n")
    before = note.read_bytes()

    result = lint_fix(vault, apply=False)

    assert len(result["fixable"]) == 1
    assert result["fixes"] == []
    assert note.read_bytes() == before


def test_fix_is_journaled_and_rollback_replays_exactly(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _entity(vault, "Nestle Thailand")
    note = _note(vault, "entities", "note-e", "# note\n\nSee [[Nestle Thailand]] for context.\n")
    before = note.read_bytes()

    lint_fix(vault, apply=True)
    journal = vault / ".constellation" / "lint-fixes.jsonl"
    assert journal.is_file()

    rollback = rollback_lint_fix(vault)

    assert rollback["rolled_back"] == 1
    assert note.read_bytes() == before  # byte-exact restore


def test_rerun_after_fix_is_clean(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _entity(vault, "Nestle Thailand")
    _note(vault, "entities", "note-f", "# note\n\nSee [[Nestle Thailand]] for context.\n")

    lint_fix(vault, apply=True)
    second = lint_fix(vault, apply=True)

    assert second["fixes"] == []
    assert second["fixable"] == []
    assert second["remaining"] == []


def test_non_mechanical_findings_untouched_by_fix(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    note = _note(vault, "entities", "note-g", "# note\n\nSee [[Nonexistent Thing]] here.\n")
    before = note.read_bytes()

    lint_fix(vault, apply=True)

    assert note.read_bytes() == before
    report = lint_records(vault)  # classic lint unaffected
    assert isinstance(report["findings"], list)


def test_rollback_without_journal_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    with pytest.raises(RecordLintError, match="journal|nothing"):
        rollback_lint_fix(vault)
