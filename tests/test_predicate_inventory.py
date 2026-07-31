"""Tests for scripts/predicate_inventory.py (Wave 0 Task 0.1).

The inventory is a read-only, metadata-only, deterministic aggregate of
predicates used by canonical relationships and entity-to-entity claims.
It must never emit note bodies, titles, record IDs, or vault paths.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from constellation.frontmatter import render_frontmatter
from constellation.models import Claim, RelationshipRecord, Sensitivity, generate_ulid
from constellation.vault import initialize_vault

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "predicate_inventory.py"

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _load_module():
    spec = importlib.util.spec_from_file_location("predicate_inventory", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(vault: Path, folder: str, record, body: str = "# note\n") -> None:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), body),
        encoding="utf-8",
    )


def _relationship(vault: Path, predicate: str, body: str = "# note\n") -> None:
    record = RelationshipRecord(
        id=generate_ulid(),
        title=f"rel {predicate}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        subject_id=generate_ulid(),
        predicate=predicate,
        object_id=generate_ulid(),
        source_ids=[generate_ulid()],
        evidence_class="user-asserted",
        created_at=NOW,
        updated_at=NOW,
    )
    _write(vault, "relationships", record, body)


def _claim(vault: Path, predicate: str, *, entity_object: bool) -> None:
    record = Claim(
        id=generate_ulid(),
        title=f"claim {predicate}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        subject_id=generate_ulid(),
        predicate=predicate,
        object_id=generate_ulid() if entity_object else None,
        object_literal=None if entity_object else "some literal value",
        source_ids=[generate_ulid()],
        created_at=NOW,
        updated_at=NOW,
    )
    _write(vault, "claims", record)


def _fixture_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _relationship(vault, "owns")
    _relationship(vault, "owns")
    _relationship(vault, "advises")
    _claim(vault, "competes_with", entity_object=True)
    _claim(vault, "competes_with", entity_object=True)
    _claim(vault, "employed_by", entity_object=True)
    _claim(vault, "has_headquarters", entity_object=False)
    return vault


def test_counts_predicates_by_record_type(tmp_path: Path) -> None:
    vault = _fixture_vault(tmp_path)
    module = _load_module()

    inventory = module.inventory_predicates(vault)

    relationships = inventory["record_types"]["relationships"]
    assert relationships["predicate_counts"] == {"advises": 1, "owns": 2}
    assert relationships["records_scanned"] == 3
    claims = inventory["record_types"]["claims_entity_to_entity"]
    assert claims["predicate_counts"] == {"competes_with": 2, "employed_by": 1}
    assert claims["records_scanned"] == 3


def test_object_literal_claims_are_excluded(tmp_path: Path) -> None:
    vault = _fixture_vault(tmp_path)
    module = _load_module()

    inventory = module.inventory_predicates(vault)

    counts = inventory["record_types"]["claims_entity_to_entity"]["predicate_counts"]
    assert "has_headquarters" not in counts


def test_output_contains_no_note_bodies_titles_or_ids(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    marker = "Zxqwv-ultra-private-marker-9831"
    _relationship(vault, "owns", body=f"# secret\n\n{marker}\n")
    module = _load_module()

    text = module.render_inventory(module.inventory_predicates(vault))

    assert marker not in text
    assert "rel owns" not in text  # titles are not emitted
    # No 26-char ULID-shaped tokens in the aggregate output.
    for token in text.replace('"', " ").split():
        assert not (len(token) == 26 and token.isalnum() and token.isupper())


def test_output_is_byte_identical_across_runs(tmp_path: Path) -> None:
    vault = _fixture_vault(tmp_path)
    module = _load_module()

    first = module.render_inventory(module.inventory_predicates(vault))
    second = module.render_inventory(module.inventory_predicates(vault))

    assert first == second
    parsed = json.loads(first)
    assert list(parsed["record_types"]["relationships"]["predicate_counts"]) == sorted(
        parsed["record_types"]["relationships"]["predicate_counts"]
    )


def test_unparseable_records_are_counted_as_skipped(tmp_path: Path) -> None:
    vault = _fixture_vault(tmp_path)
    (vault / "relationships" / "broken.md").write_text("not frontmatter at all", encoding="utf-8")
    module = _load_module()

    inventory = module.inventory_predicates(vault)

    assert inventory["record_types"]["relationships"]["records_skipped"] == 1
    assert inventory["record_types"]["relationships"]["records_scanned"] == 3


def test_missing_folders_yield_empty_inventory(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    module = _load_module()

    inventory = module.inventory_predicates(vault)

    for section in inventory["record_types"].values():
        assert section["predicate_counts"] == {}
        assert section["records_scanned"] == 0


def test_cli_emits_deterministic_json(tmp_path: Path) -> None:
    vault = _fixture_vault(tmp_path)

    first = subprocess.run(
        [sys.executable, str(SCRIPT), str(vault)],
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, str(SCRIPT), str(vault)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    parsed = json.loads(first.stdout)
    assert parsed["record_types"]["relationships"]["predicate_counts"]["owns"] == 2
    assert str(vault) not in first.stdout
