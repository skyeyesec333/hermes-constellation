import hashlib
import json
from pathlib import Path

import pytest

from constellation.cli import main
from constellation.migration import MigrationError, inventory_vault, plan_migration

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
