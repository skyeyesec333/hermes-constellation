"""Tests for entity-resolution warninglists (i2-successor Wave 2 Task 2.2)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.entity_resolution import scan_entity_duplicates_report
from constellation.entity_warninglists import (
    EntityWarninglistError,
    check_value,
    load_vault_warninglists,
    load_warninglists,
)
from constellation.frontmatter import render_frontmatter
from constellation.models import EntityKind, EntityRecord, Sensitivity, generate_ulid
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _write(vault: Path, folder: str, record) -> None:
    target = vault / folder / f"{record.id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n"),
        encoding="utf-8",
    )


def _entity(vault: Path, title: str, kind: EntityKind = EntityKind.COMPANY) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=kind, title=title, status="active",
        sensitivity=Sensitivity.INTERNAL, source_ids=[], created_at=NOW, updated_at=NOW,
    )
    folder = "people" if kind == EntityKind.PERSON else "entities"
    _write(vault, folder, record)
    return record.id


def _write_list(path: Path, lists: list[dict]) -> Path:
    path.write_text(json.dumps({"schema_version": "0.1", "lists": lists}), encoding="utf-8")
    return path


def test_default_lists_load() -> None:
    lists = load_warninglists()
    names = {item.name for item in lists}
    assert "fictional-generic-tokens" in names
    assert "fictional-ambiguous-names" in names
    assert "fictional-kind-pinned" in names


def test_exact_and_substring_matching(tmp_path: Path) -> None:
    path = _write_list(tmp_path / "lists.json", [{
        "name": "demo", "version": 1, "description": "demo",
        "match_attributes": ["exact"], "values": ["Acme Holdings"],
        "action": "suppress", "entity_kind": None,
    }])
    lists = load_warninglists(path)
    assert check_value("acme holdings", lists).decision == "suppress"
    assert check_value("acme holdings group", lists).decision == "pass"

    path = _write_list(tmp_path / "lists.json", [{
        "name": "demo", "version": 1, "description": "demo",
        "match_attributes": ["substring"], "values": ["acme"],
        "action": "suppress", "entity_kind": None,
    }])
    lists = load_warninglists(path)
    assert check_value("ACME Holdings Group", lists).decision == "suppress"


def test_hostname_and_regex_matching(tmp_path: Path) -> None:
    path = _write_list(tmp_path / "lists.json", [{
        "name": "hosts", "version": 1, "description": "demo",
        "match_attributes": ["hostname"], "values": ["example.test"],
        "action": "suppress", "entity_kind": None,
    }])
    lists = load_warninglists(path)
    assert check_value("https://mail.example.test/page", lists).decision == "suppress"
    assert check_value("https://other.sample.invalid/", lists).decision == "pass"

    path = _write_list(tmp_path / "lists.json", [{
        "name": "patterns", "version": 1, "description": "demo",
        "match_attributes": ["regex"], "values": [r"^holding\s+company\s+\d+$"],
        "action": "suppress", "entity_kind": None,
    }])
    lists = load_warninglists(path)
    assert check_value("Holding Company 42", lists).decision == "suppress"
    assert check_value("Holding Company Alpha", lists).decision == "pass"


def test_force_ambiguity_and_require_kind(tmp_path: Path) -> None:
    path = _write_list(tmp_path / "lists.json", [
        {
            "name": "amb", "version": 1, "description": "demo",
            "match_attributes": ["exact"], "values": ["john smith"],
            "action": "force_ambiguity", "entity_kind": None,
        },
        {
            "name": "pinned", "version": 1, "description": "demo",
            "match_attributes": ["exact"], "values": ["apple"],
            "action": "require_kind", "entity_kind": "company",
        },
    ])
    lists = load_warninglists(path)
    assert check_value("John Smith", lists, entity_kind="person").decision == "force_ambiguity"
    assert check_value("apple", lists, entity_kind="company").decision == "pass"
    decision = check_value("apple", lists, entity_kind="person")
    assert decision.decision == "suppress"
    assert "requires entity kind" in decision.reason


def test_invalid_list_fails_closed(tmp_path: Path) -> None:
    path = _write_list(tmp_path / "lists.json", [{
        "name": "broken", "version": 1, "description": "demo",
        "match_attributes": ["regex"], "values": ["[unclosed"],
        "action": "suppress", "entity_kind": None,
    }])
    with pytest.raises(EntityWarninglistError):
        load_warninglists(path)
    path = _write_list(tmp_path / "lists.json", [{
        "name": "broken", "version": 1, "description": "demo",
        "match_attributes": ["exact"], "values": ["x"],
        "action": "require_kind", "entity_kind": None,
    }])
    with pytest.raises(EntityWarninglistError):
        load_warninglists(path)


def test_vault_local_list_merges(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    extra = vault / ".constellation" / "entity-warninglists.json"
    _write_list(extra, [{
        "name": "private-ops", "version": 1, "description": "demo",
        "match_attributes": ["exact"], "values": ["internal codename"],
        "action": "suppress", "entity_kind": None,
    }])
    lists = load_vault_warninglists(vault)
    assert check_value("internal codename", lists).decision == "suppress"
    assert check_value("unknown entity", lists).decision == "suppress"  # default still active


def test_duplicate_scan_report_shows_suppressions(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    # Exact-title duplicate on a default-list suppressed token.
    _entity(vault, "Unknown Entity")
    _entity(vault, "Unknown Entity")
    # Exact-title duplicate on an ambiguity-listed name.
    _entity(vault, "John Smith", EntityKind.PERSON)
    _entity(vault, "John Smith", EntityKind.PERSON)
    # Clean duplicate that survives.
    _entity(vault, "Fictional Holdings")
    _entity(vault, "Fictional Holdings")

    report = scan_entity_duplicates_report(vault)

    suppressed_titles = {item["proposed_title"] for item in report["suppressed"]}
    assert "Unknown Entity" in suppressed_titles
    ambiguous = {item["proposed_title"] for item in report["ambiguous"]}
    assert "John Smith" in ambiguous
    remaining = {dup.proposed_title for dup in report["duplicates"]}
    assert "Fictional Holdings" in remaining
    assert "Unknown Entity" not in remaining
    # Suppression is visible, never silent.
    assert all(item["reason"] for item in report["suppressed"])
