import json

import pytest

import constellation.apply as apply_module
from constellation.apply import ApplyError, activate_cutover, build_cutover_vault, tree_sha256
from constellation.migration import rehearse_migration
from constellation.validation import validate_vault
from constellation.vault import STARTER_FOLDERS, is_initialized


def _legacy_record(record_type: str, title_key: str, title: str, record_id: str) -> str:
    return f"""---
id: {record_id}
type: {record_type}
{title_key}: {title}
status: active
sensitivity: internal
last_updated: 2026-01-03
---

# {title}

Preserved body.
"""


def test_build_cutover_preserves_working_paths_and_isolates_invalid_canonical_notes(tmp_path):
    source = tmp_path / "vault"
    (source / "people").mkdir(parents=True)
    (source / "patterns").mkdir()
    (source / "source-items").mkdir()
    (source / "Attachments").mkdir()
    (source / ".obsidian").mkdir()
    (source / ".constellation").mkdir()
    (source / "people/alex.md").write_text(
        _legacy_record("person", "name", "Alex", "legacy-person"), encoding="utf-8"
    )
    (source / "patterns/keep.md").write_text(
        _legacy_record("pattern", "title", "Keep", "legacy-pattern"), encoding="utf-8"
    )
    source_item = _legacy_record(
        "source-item", "title", "Evidence", "legacy-source"
    )
    (source / "source-items/evidence.md").write_text(source_item, encoding="utf-8")
    (source / "source-items/broken.md").write_text("# Broken legacy note\n", encoding="utf-8")
    (source / "HOME.md").write_text("# Home\n", encoding="utf-8")
    (source / "Attachments/brief.txt").write_text("source bytes\n", encoding="utf-8")
    (source / ".obsidian/workspace.json").write_text('{"ok": true}\n', encoding="utf-8")
    (source / ".constellation/config.yaml").write_text("version: 1\n", encoding="utf-8")

    expected_hash = tree_sha256(source)
    rehearsal = tmp_path / "rehearsal"
    rehearse_migration(source, rehearsal, confirm_disposable=True)
    destination = tmp_path / "prepared"

    result = build_cutover_vault(
        source,
        rehearsal,
        destination,
        expected_source_sha256=expected_hash,
        confirm_apply_staging=True,
    )

    assert result["source_writes_performed"] is False
    assert result["candidate_validation"] == {"valid": 2, "invalid": 0}
    assert tree_sha256(source) == expected_hash
    assert (destination / "entities/person-alex.md").is_file()
    assert (destination / "source-items/evidence.md").is_file()
    assert (destination / "sources/legacy-source-items/evidence.md").read_text() == source_item
    assert (destination / "people/alex.md").is_file()
    assert (destination / "patterns/keep.md").is_file()
    assert (destination / "quarantine/source-items/broken.md").read_text() == "# Broken legacy note\n"
    assert not (destination / "source-items/broken.md").exists()
    assert (destination / "HOME.md").read_text() == "# Home\n"
    assert (destination / "Attachments/brief.txt").read_text() == "source bytes\n"
    assert (destination / ".obsidian/workspace.json").is_file()
    assert is_initialized(destination) is True
    assert (destination / ".migration/legacy-config.yaml").read_text() == "version: 1\n"
    assert all((destination / folder).is_dir() for folder in STARTER_FOLDERS)
    assert (destination / ".migration/apply-manifest.private.json").is_file()
    assert validate_vault(destination, limit=100)["invalid"] == 0
    manifest = json.loads((destination / ".migration/apply-manifest.private.json").read_text())
    assert manifest["source_tree_sha256"] == expected_hash
    assert manifest["manual_symlinks"] == []


def test_activate_cutover_atomically_retains_original_as_rollback(tmp_path):
    canonical = tmp_path / "constellation"
    prepared = tmp_path / "constellation.prepared"
    rollback = tmp_path / "constellation.rollback"
    canonical.mkdir()
    (canonical / "old.txt").write_text("old vault\n", encoding="utf-8")
    prepared.mkdir()
    (prepared / "entities").mkdir()
    (prepared / ".constellation").mkdir()
    (prepared / ".constellation/config.yaml").write_text(
        "kind: constellation-vault\nschema_version: '0.1'\n", encoding="utf-8"
    )
    (prepared / ".migration").mkdir()
    expected_hash = tree_sha256(canonical)
    (prepared / ".migration/apply-manifest.private.json").write_text(
        json.dumps(
            {
                "source_tree_sha256": expected_hash,
                "candidate_validation": {"valid": 0, "invalid": 0},
                "candidate_record_count": 0,
                "candidate_ids_unique": True,
            }
        ),
        encoding="utf-8",
    )

    result = activate_cutover(
        canonical,
        prepared,
        rollback,
        expected_source_sha256=expected_hash,
        confirm_canonical_apply=True,
    )

    assert result["activated"] is True
    assert not prepared.exists()
    assert (canonical / ".migration/apply-manifest.private.json").is_file()
    assert (rollback / "old.txt").read_text() == "old vault\n"


def test_activate_cutover_restores_both_paths_if_post_swap_verification_fails(
    tmp_path, monkeypatch
):
    canonical = tmp_path / "constellation"
    prepared = tmp_path / "constellation.prepared"
    rollback = tmp_path / "constellation.rollback"
    canonical.mkdir()
    (canonical / "old.txt").write_text("old vault\n", encoding="utf-8")
    prepared.mkdir()
    (prepared / "new.txt").write_text("new vault\n", encoding="utf-8")
    (prepared / ".migration").mkdir()
    expected_hash = tree_sha256(canonical)
    (prepared / ".migration/apply-manifest.private.json").write_text(
        json.dumps(
            {
                "source_tree_sha256": expected_hash,
                "candidate_validation": {"valid": 0, "invalid": 0},
                "candidate_record_count": 0,
                "candidate_ids_unique": True,
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def fail_after_swap(root, manifest):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ApplyError("injected post-swap failure")
        return {"valid": 0, "invalid": 0}

    monkeypatch.setattr(apply_module, "_verify_prepared_vault", fail_after_swap)

    with pytest.raises(ApplyError, match="injected post-swap failure"):
        activate_cutover(
            canonical,
            prepared,
            rollback,
            expected_source_sha256=expected_hash,
            confirm_canonical_apply=True,
        )

    assert calls == 2
    assert (canonical / "old.txt").read_text() == "old vault\n"
    assert (prepared / "new.txt").read_text() == "new vault\n"
    assert not rollback.exists()
