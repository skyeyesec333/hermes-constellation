import hashlib
import json
from pathlib import Path

import pytest

import constellation.migration as migration_module
from constellation.cli import main
from constellation.frontmatter import parse_frontmatter
from constellation.migration import (
    MigrationError,
    build_mapping_plan,
    inventory_vault,
    parse_legacy_frontmatter,
    plan_migration,
    rehearse_migration,
)
from constellation.validation import validate_canonical_text

RECORD_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _record(title: str, record_id: str = RECORD_ID) -> str:
    return f"""---
schema_version: '0.1'
id: {record_id}
type: person
title: {title}
status: active
sensitivity: internal
created_at: 2026-01-01T00:00:00Z
updated_at: 2026-01-01T00:00:00Z
---

# {title}
"""


def _snapshot(root: Path) -> dict[str, tuple[str, str]]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", str(path.readlink()))
        elif path.is_file():
            result[relative] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            result[relative] = ("directory", "")
    return result


def make_legacy_vault(tmp_path: Path) -> Path:
    root = tmp_path / "legacy-vault"
    (root / "entities").mkdir(parents=True)
    (root / "People").mkdir()
    (root / "Attachments").mkdir()
    (root / ".constellation/state").mkdir(parents=True)
    (root / ".obsidian/plugins/example").mkdir(parents=True)
    (root / ".trash").mkdir()
    (root / "indexes/generated").mkdir(parents=True)
    (root / "entities/person-one.md").write_text(_record("One"), encoding="utf-8")
    (root / "People/person-two.md").write_text(_record("Two"), encoding="utf-8")
    (root / "People/unstructured.md").write_text("# No frontmatter\n", encoding="utf-8")
    (root / "Attachments/brief.txt").write_text("source", encoding="utf-8")
    (root / ".constellation/state/index.sqlite3").write_bytes(b"derived")
    (root / ".obsidian/plugins/example/main.js").write_text("plugin", encoding="utf-8")
    (root / ".trash/deleted.md").write_text("# Deleted", encoding="utf-8")
    (root / "indexes/generated/search.json").write_text("{}", encoding="utf-8")
    (root / "._metadata").write_bytes(b"apple-double")
    (root / "linked-private").symlink_to(tmp_path / "outside", target_is_directory=True)
    return root


def test_inventory_is_read_only_and_reports_structure_without_note_bodies(tmp_path):
    root = make_legacy_vault(tmp_path)
    before = _snapshot(root)

    report = inventory_vault(root)

    assert _snapshot(root) == before
    assert report["scanned_files"] == 4
    assert report["markdown_files"] == 3
    assert report["canonical_markdown"] == 1
    assert report["legacy_markdown"] == 2
    assert report["other_files"] == 1
    assert report["ignored_internal_files"] == 1
    assert report["ignored_operational_files"] == 4
    assert report["symlinks"] == ["linked-private"]
    assert report["frontmatter"] == {"valid": 2, "missing": 1, "invalid": 0, "oversized": 0}
    assert report["duplicate_ids"] == {RECORD_ID: ["People/person-two.md", "entities/person-one.md"]}
    assert "No frontmatter" not in str(report)
    assert "source" not in str(report)


def test_dry_run_plan_has_bounded_explicit_actions_and_no_writes(tmp_path):
    root = make_legacy_vault(tmp_path)
    before = _snapshot(root)

    plan = plan_migration(root, action_limit=3)

    assert _snapshot(root) == before
    assert plan["mode"] == "dry-run"
    assert plan["writes_performed"] is False
    assert plan["actions_truncated"] is True
    action_types = {action["action"] for action in plan["actions"]}
    assert action_types <= {
        "assign_metadata",
        "map_legacy_record",
        "preserve_source",
        "resolve_duplicate_id",
        "manual_symlink_review",
    }
    assert plan["summary"]["total_actions"] > len(plan["actions"])


def test_inventory_rejects_symlink_root_and_file_limit(tmp_path):
    root = make_legacy_vault(tmp_path)
    linked = tmp_path / "linked-vault"
    linked.symlink_to(root, target_is_directory=True)

    with pytest.raises(MigrationError, match="symlink"):
        inventory_vault(linked)
    with pytest.raises(MigrationError, match="file limit"):
        inventory_vault(root, max_files=2)


def test_migration_plan_cli_is_machine_readable(tmp_path, capsys):
    root = make_legacy_vault(tmp_path)

    assert main(["migrate-plan", str(root), "--action-limit", "2"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["mode"] == "dry-run"
    assert len(payload["result"]["actions"]) == 2


def test_plan_flags_existing_canonical_notes_that_fail_specialized_schema(tmp_path):
    root = tmp_path / "vault"
    (root / "claims").mkdir(parents=True)
    (root / "claims/broken.md").write_text(
        _record("Missing claim fields").replace("type: person", "type: claim"),
        encoding="utf-8",
    )

    plan = plan_migration(root)

    assert plan["inventory"]["canonical_validation"] == {"valid": 0, "invalid": 1}
    assert any(
        action["action"] == "repair_canonical_record" and action["path"] == "claims/broken.md"
        for action in plan["actions"]
    )


def _legacy_record(title: str, record_type: str, sensitivity: str, legacy_id: str) -> str:
    return f"""---
id: {legacy_id}
type: {record_type}
name: {title}
status: active
sensitivity: {sensitivity}
last_updated: 2026-01-02
---

# {title}

PRIVATE BODY MUST NOT ENTER THE PLAN
"""


def test_legacy_parser_normalizes_known_auto_discovery_marker_without_changing_body():
    text = """---
id: company-example
type: company
company_name: Example
sensitivity: normal
source_count: 2
--- auto-discovered degree-2 skeleton below confidence threshold
source_count: 3
enrichment_date: 2026-01-03
---

# Example

Body stays byte-for-byte.
"""

    metadata, body, repair = parse_legacy_frontmatter(text)

    assert metadata["id"] == "company-example"
    assert metadata["source_count"] == 2
    assert str(metadata["enrichment_date"]) == "2026-01-03"
    assert repair == {
        "kind": "auto-discovery-marker",
        "conflicting_keys": ["source_count"],
    }
    assert body == "\n# Example\n\nBody stays byte-for-byte.\n"


def test_mapping_plan_promotes_repairable_entity_marker_to_candidate(tmp_path):
    root = tmp_path / "vault"
    (root / "companies").mkdir(parents=True)
    text = """---
id: legacy-company
type: company
company_name: Example
status: active
sensitivity: normal
last_updated: 2026-01-03
--- auto-discovered degree-2 skeleton below confidence threshold
source_count: 2
enrichment_date: 2026-01-03
---

# Example

Entity body remains unchanged.
"""
    (root / "companies/example.md").write_text(text, encoding="utf-8")

    plan = build_mapping_plan(root)
    mapping = plan["mappings"][0]

    assert mapping["disposition"] == "candidate_entity"
    assert mapping["repair"] == {
        "kind": "auto-discovery-marker",
        "conflicting_keys": [],
    }
    assert mapping["proposed_metadata"]["sensitivity"] == "internal"
    assert "Entity body remains" not in json.dumps(plan)


def test_repairable_duplicate_ids_are_remapped_per_path(tmp_path):
    root = tmp_path / "vault"
    (root / "companies").mkdir(parents=True)
    template = """---
id: duplicated-legacy-id
type: company
company_name: {name}
status: active
sensitivity: internal
last_updated: 2026-01-03
--- auto-discovered degree-2 skeleton below confidence threshold
source_count: 1
---

# {name}
"""
    (root / "companies/alpha.md").write_text(template.format(name="Alpha"), encoding="utf-8")
    (root / "companies/beta.md").write_text(template.format(name="Beta"), encoding="utf-8")

    plan = build_mapping_plan(root)
    ids = [mapping["proposed_metadata"]["id"] for mapping in plan["mappings"]]

    assert len(ids) == len(set(ids)) == 2


def test_source_item_mapper_hashes_preserved_legacy_note_and_retains_body(tmp_path):
    root = tmp_path / "vault"
    (root / "source-items").mkdir(parents=True)
    text = """---
id: legacy-source
type: source_item
title: Interview note
status: active
sensitivity: low
last_updated: 2026-01-03
source_url: https://example.invalid/interview
---

# Interview

Evidence body remains unchanged.
"""
    source = root / "source-items/interview.md"
    source.write_text(text, encoding="utf-8")

    plan = build_mapping_plan(root)
    mapping = plan["mappings"][0]

    assert mapping["disposition"] == "candidate_source_item"
    assert mapping["target_path"] == "source-items/source-item-interview.md"
    assert mapping["proposed_metadata"]["source_hash"] == hashlib.sha256(text.encode()).hexdigest()
    assert mapping["proposed_metadata"]["original_path"] == (
        "sources/legacy-source-items/interview.md"
    )
    assert mapping["proposed_metadata"]["media_type"] == "text/markdown"
    assert mapping["proposed_metadata"]["sensitivity"] == "internal"
    assert mapping["proposed_metadata"]["source_url"] == "https://example.invalid/interview"
    assert "Evidence body remains" not in json.dumps(plan)

    destination = tmp_path / "rehearsal"
    rehearse_migration(root, destination, confirm_disposable=True)
    candidate = destination / "candidate-vault/source-items/source-item-interview.md"
    original = destination / "candidate-vault/sources/legacy-source-items/interview.md"
    assert original.read_bytes() == text.encode()
    _, source_body = parse_frontmatter(text)
    _, candidate_body = parse_frontmatter(candidate.read_text(encoding="utf-8"))
    assert candidate_body == source_body
    validate_canonical_text(candidate.read_text(encoding="utf-8"), "source-items/source-item-interview.md")


def test_source_items_folder_entity_skeleton_is_mapped_as_entity(tmp_path):
    root = tmp_path / "vault"
    (root / "source-items").mkdir(parents=True)
    text = """---
id: legacy-company
type: company
company_name: Skeleton Company
status: active
sensitivity: internal
last_updated: 2026-01-03
---

# Skeleton Company
"""
    (root / "source-items/skeleton.md").write_text(text, encoding="utf-8")

    mapping = build_mapping_plan(root)["mappings"][0]

    assert mapping["disposition"] == "candidate_entity"
    assert mapping["target_path"] == "entities/company-skeleton.md"
    assert mapping["proposed_metadata"]["type"] == "company"


def test_mapping_plan_applies_privacy_safe_defaults_and_deterministic_ids(tmp_path):
    root = tmp_path / "vault"
    for folder in ("people", "companies", "patterns"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "people/alex.md").write_text(
        _legacy_record("Alex", "person", "normal", "shared-legacy-id"), encoding="utf-8"
    )
    (root / "companies/acme.md").write_text(
        _legacy_record("Acme", "company", "confidential", "shared-legacy-id"), encoding="utf-8"
    )
    (root / "patterns/repeated.md").write_text(
        _legacy_record("Repeated", "pattern", "low", "pattern-family"), encoding="utf-8"
    )

    first = build_mapping_plan(root)
    second = build_mapping_plan(root)
    mappings = {item["source_path"]: item for item in first["mappings"]}

    assert first == second
    assert mappings["people/alex.md"]["disposition"] == "candidate_entity"
    assert mappings["people/alex.md"]["target_path"] == "entities/person-alex.md"
    assert mappings["people/alex.md"]["proposed_metadata"]["sensitivity"] == "internal"
    assert mappings["companies/acme.md"]["proposed_metadata"]["sensitivity"] == "confidential"
    assert mappings["patterns/repeated.md"]["disposition"] == "preserve_legacy"
    assert mappings["patterns/repeated.md"]["proposed_sensitivity"] == "internal"
    assert mappings["people/alex.md"]["proposed_metadata"]["id"] != mappings["companies/acme.md"][
        "proposed_metadata"
    ]["id"]
    assert len(mappings["people/alex.md"]["proposed_metadata"]["id"]) == 26
    assert "PRIVATE BODY" not in json.dumps(first)


def test_mapping_plan_resolves_candidate_target_collisions_deterministically(tmp_path):
    root = tmp_path / "vault"
    (root / "people").mkdir(parents=True)
    (root / "people/Alpha.md").write_text(
        _legacy_record("Alpha One", "person", "internal", "legacy-one"), encoding="utf-8"
    )
    (root / "people/alpha!.md").write_text(
        _legacy_record("Alpha Two", "person", "internal", "legacy-two"), encoding="utf-8"
    )

    plan = build_mapping_plan(root)
    targets = [item["target_path"] for item in plan["mappings"]]

    assert len(targets) == len(set(targets))
    assert targets == sorted(targets)


def test_rehearsal_writes_only_to_disposable_destination_and_preserves_every_input(tmp_path):
    root = tmp_path / "vault"
    (root / "people").mkdir(parents=True)
    (root / "patterns").mkdir()
    (root / "Attachments").mkdir()
    person = _legacy_record("Alex", "person", "normal", "legacy-person")
    pattern = _legacy_record("Pattern", "pattern", "low", "legacy-pattern")
    broken = "# Missing frontmatter\n"
    (root / "people/alex.md").write_text(person, encoding="utf-8")
    (root / "people/broken.md").write_text(broken, encoding="utf-8")
    (root / "patterns/pattern.md").write_text(pattern, encoding="utf-8")
    (root / "Attachments/brief.txt").write_text("source bytes", encoding="utf-8")
    (root / "external-link").symlink_to(tmp_path / "outside", target_is_directory=True)
    before = _snapshot(root)
    destination = tmp_path / "rehearsal"

    result = rehearse_migration(root, destination, confirm_disposable=True)

    assert _snapshot(root) == before
    assert result["source_writes_performed"] is False
    assert result["destination_writes_performed"] is True
    assert (destination / "preserved/people/alex.md").read_text(encoding="utf-8") == person
    assert (destination / "candidate-vault/legacy/patterns/pattern.md").read_text(
        encoding="utf-8"
    ) == pattern
    assert (destination / "candidate-vault/quarantine/people/broken.md").read_text(
        encoding="utf-8"
    ) == broken
    assert (destination / "candidate-vault/sources/Attachments/brief.txt").read_text(
        encoding="utf-8"
    ) == "source bytes"
    candidate = destination / "candidate-vault/entities/person-alex.md"
    validate_canonical_text(candidate.read_text(encoding="utf-8"), "entities/person-alex.md")
    assert not (destination / "candidate-vault/external-link").exists()
    assert (destination / "migration-plan.private.json").is_file()
    assert (destination / "migration-journal.private.json").is_file()


def test_rehearsal_materializes_repairable_entity_without_body_changes(tmp_path):
    root = tmp_path / "vault"
    (root / "companies").mkdir(parents=True)
    text = """---
id: legacy-company
type: company
company_name: Example
status: active
sensitivity: normal
last_updated: 2026-01-03
--- auto-discovered degree-2 skeleton below confidence threshold
source_count: 2
enrichment_date: 2026-01-03
---

# Example

Body remains exact.
"""
    (root / "companies/example.md").write_text(text, encoding="utf-8")
    destination = tmp_path / "rehearsal"

    rehearse_migration(root, destination, confirm_disposable=True)

    _, source_body, _ = parse_legacy_frontmatter(text)
    candidate = destination / "candidate-vault/entities/company-example.md"
    _, candidate_body = parse_frontmatter(candidate.read_text(encoding="utf-8"))
    assert candidate_body == source_body
    validate_canonical_text(candidate.read_text(encoding="utf-8"), "entities/company-example.md")


def test_rehearsal_rejects_unconfirmed_overlapping_existing_and_symlink_destinations(tmp_path):
    root = tmp_path / "vault"
    (root / "people").mkdir(parents=True)
    (root / "people/alex.md").write_text(
        _legacy_record("Alex", "person", "internal", "legacy-person"), encoding="utf-8"
    )
    before = _snapshot(root)

    with pytest.raises(MigrationError, match="confirmation"):
        rehearse_migration(root, tmp_path / "unconfirmed")
    with pytest.raises(MigrationError, match="overlap"):
        rehearse_migration(root, root / "nested-output", confirm_disposable=True)
    assert _snapshot(root) == before

    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(MigrationError, match="must not exist"):
        rehearse_migration(root, existing, confirm_disposable=True)
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"

    linked = tmp_path / "linked-output"
    linked.symlink_to(tmp_path / "somewhere", target_is_directory=True)
    with pytest.raises(MigrationError, match="symlink"):
        rehearse_migration(root, linked, confirm_disposable=True)


def test_rehearsal_cli_requires_explicit_confirmation_and_returns_json(tmp_path, capsys):
    root = tmp_path / "vault"
    (root / "people").mkdir(parents=True)
    (root / "people/alex.md").write_text(
        _legacy_record("Alex", "person", "internal", "legacy-person"), encoding="utf-8"
    )
    destination = tmp_path / "rehearsal"

    assert main(["migrate-rehearse", str(root), str(destination), "--confirm-disposable"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["source_writes_performed"] is False
    assert payload["result"]["destination_writes_performed"] is True
    assert destination.is_dir()


def test_rehearsal_aborts_if_source_tree_changes_after_copy(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    (root / "people").mkdir(parents=True)
    (root / "people/alex.md").write_text(
        _legacy_record("Alex", "person", "internal", "legacy-person"), encoding="utf-8"
    )
    destination = tmp_path / "rehearsal"
    original_write = migration_module._write_rehearsal_file

    def write_then_add_source(stage, relative, data):
        result = original_write(stage, relative, data)
        if relative == "migration-journal.private.json":
            (root / "people/appeared.md").write_text(
                _legacy_record("Appeared", "person", "internal", "appeared"), encoding="utf-8"
            )
        return result

    monkeypatch.setattr(migration_module, "_write_rehearsal_file", write_then_add_source)

    with pytest.raises(MigrationError, match="source tree changed"):
        rehearse_migration(root, destination, confirm_disposable=True)
    assert not destination.exists()
