import hashlib
import json
import os
from datetime import UTC, datetime

import pytest
import yaml

from constellation.doctor import doctor_json, doctor_report
from constellation.frontmatter import render_frontmatter
from constellation.models import EntityKind, EntityRecord, Interaction, Sensitivity
from constellation.storage import ConflictError, UnsafePathError, atomic_write_text, safe_relative_path
from constellation.vault import VaultInitializationError, initialize_vault


def test_initialize_fresh_vault_and_idempotent_rerun(tmp_path):
    root = tmp_path / "vault"
    created = initialize_vault(root)
    assert root / ".constellation/config.yaml" in created
    assert (root / "Library/Files").is_dir()
    assert (root / "source-items").is_dir()
    config = yaml.safe_load((root / ".constellation/config.yaml").read_text(encoding="utf-8"))
    assert config["egress"] == {"external_enabled": False, "providers": {}}
    assert config["source_registration"] == "review"
    assert initialize_vault(root) == []


def test_initialize_refuses_nonempty_symlink_and_nested_targets(tmp_path):
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "user.md").write_text("mine", encoding="utf-8")
    with pytest.raises(VaultInitializationError):
        initialize_vault(nonempty)

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(VaultInitializationError):
        initialize_vault(link)

    parent = tmp_path / "parent"
    initialize_vault(parent)
    with pytest.raises(VaultInitializationError):
        initialize_vault(parent / "nested")


def test_relative_containment_and_symlink_components(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    with pytest.raises(UnsafePathError):
        safe_relative_path(root, "../escape.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "jump").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePathError):
        safe_relative_path(root, "jump/file.txt")


def test_atomic_write_honors_expected_hash(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    path = atomic_write_text(root, "claims/one.md", "first")
    digest = hashlib.sha256(b"first").hexdigest()
    atomic_write_text(root, "claims/one.md", "second", expected_hash=digest)
    with pytest.raises(ConflictError):
        atomic_write_text(root, "claims/one.md", "third", expected_hash=digest)
    assert path.read_text(encoding="utf-8") == "second"


def test_atomic_write_detects_change_during_staging(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    initialize_vault(root)
    target = atomic_write_text(root, "claims/one.md", "reviewed")
    reviewed_hash = hashlib.sha256(b"reviewed").hexdigest()
    real_fsync = os.fsync
    changed = False

    def concurrent_change(descriptor):
        nonlocal changed
        if not changed:
            changed = True
            target.write_text("concurrent", encoding="utf-8")
        return real_fsync(descriptor)

    monkeypatch.setattr("constellation.storage.os.fsync", concurrent_change)
    with pytest.raises(ConflictError):
        atomic_write_text(root, "claims/one.md", "proposed", expected_hash=reviewed_hash)
    assert target.read_text(encoding="utf-8") == "concurrent"


def test_atomic_create_only_detects_target_created_while_staged(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    initialize_vault(root)
    target = root / "claims/one.md"
    real_fsync = os.fsync
    created = False

    def concurrent_create(descriptor):
        nonlocal created
        if not created:
            created = True
            target.write_text("concurrent", encoding="utf-8")
        return real_fsync(descriptor)

    monkeypatch.setattr("constellation.storage.os.fsync", concurrent_create)
    with pytest.raises(ConflictError):
        atomic_write_text(root, "claims/one.md", "proposed", must_not_exist=True)

    assert target.read_text(encoding="utf-8") == "concurrent"
    assert list(target.parent.glob(".one.md.*")) == []


def test_doctor_returns_json_capability_report(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    report = doctor_report(root)
    assert report["schema_version"] == "0.1"
    assert report["vault"]["initialized"] is True
    assert isinstance(report["capabilities"]["sqlite_fts5"], bool)
    assert report["capabilities"]["text_ingest"] is True
    for capability in (
        "pdf_pymupdf",
        "pdf_scanned_rapidocr",
        "docx_python_docx",
        "pptx_python_pptx",
        "pptx_markitdown",
        "xlsx_openpyxl",
        "image_rapidocr",
        "mime_libmagic",
    ):
        assert isinstance(report["capabilities"][capability], bool)
    assert report["capabilities"]["ooxml_archive_safety"] is True
    assert json.loads(doctor_json(root)) == report


def test_doctor_separates_pipeline_relevant_crm_coverage_from_research_only_entities(
    tmp_path,
):
    root = tmp_path / "vault"
    initialize_vault(root)
    now = datetime.now(UTC)

    pipeline = EntityRecord(
        type=EntityKind.PERSON,
        title="Pipeline Person",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
    )
    interacted = EntityRecord(
        type=EntityKind.COMPANY,
        title="Interacted Company",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
    )
    research_only = EntityRecord(
        type=EntityKind.COMPANY,
        title="Research Only Company",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
    )
    for entity, body in (
        (pipeline, "pipeline_stage:: opportunity\nnext_action:: Follow up\n"),
        (interacted, "No CRM fields yet.\n"),
        (research_only, "Background research only.\n"),
    ):
        atomic_write_text(
            root,
            f"entities/{entity.id}.md",
            render_frontmatter(entity.model_dump(mode="json"), body),
        )

    interaction = Interaction(
        title="One meeting",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        subject_ids=[interacted.id],
        summary="Met the company.",
        occurred_at=now,
        created_at=now,
        updated_at=now,
    )
    atomic_write_text(
        root,
        f"interactions/{interaction.id}.md",
        render_frontmatter(interaction.model_dump(mode="json"), "Meeting evidence.\n"),
    )

    crm = doctor_report(root)["crm_coverage"]

    assert crm == {
        "total_entities": 3,
        "with_stage": 1,
        "with_touch": 0,
        "with_action": 1,
        "coverage_pct": 33.3,
        "pipeline_relevant_entities": 2,
        "research_only_entities": 1,
        "pipeline_relevant_with_stage": 1,
        "pipeline_relevant_with_touch": 0,
        "pipeline_relevant_with_action": 1,
        "pipeline_relevant_coverage_pct": 50.0,
    }
