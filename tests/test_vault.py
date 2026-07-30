import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any, cast

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


def test_doctor_accepts_people_subjects_for_claims_and_opportunities(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    person_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    claim_id = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
    opportunity_id = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
    (root / "people").mkdir()
    (root / "people" / "person.md").write_text(
        render_frontmatter({"id": person_id, "type": "person"}, "# Person\n"),
        encoding="utf-8",
    )
    (root / "claims" / "claim.md").write_text(
        render_frontmatter(
            {"id": claim_id, "type": "claim", "subject_id": person_id, "source_ids": []},
            "# Claim\n",
        ),
        encoding="utf-8",
    )
    (root / "opportunities" / "opportunity.md").write_text(
        render_frontmatter(
            {"id": opportunity_id, "type": "opportunity", "subject_ids": [person_id]},
            "# Opportunity\n",
        ),
        encoding="utf-8",
    )

    integrity = doctor_report(root)["referential_integrity"]
    assert integrity == {"clean": True, "orphan_count": 0, "orphans": {}}


def test_doctor_still_flags_unknown_claim_and_opportunity_subjects(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    missing_claim_subject = "01ARZ3NDEKTSV4RRFFQ69G5FAY"
    missing_opportunity_subject = "01ARZ3NDEKTSV4RRFFQ69G5FAZ"
    (root / "claims" / "claim.md").write_text(
        render_frontmatter(
            {
                "id": "01ARZ3NDEKTSV4RRFFQ69G5FB0",
                "type": "claim",
                "subject_id": missing_claim_subject,
                "source_ids": [],
            },
            "# Claim\n",
        ),
        encoding="utf-8",
    )
    (root / "opportunities" / "opportunity.md").write_text(
        render_frontmatter(
            {
                "id": "01ARZ3NDEKTSV4RRFFQ69G5FB1",
                "type": "opportunity",
                "subject_ids": [missing_opportunity_subject],
            },
            "# Opportunity\n",
        ),
        encoding="utf-8",
    )

    integrity = doctor_report(root)["referential_integrity"]
    assert integrity == {
        "clean": False,
        "orphan_count": 2,
        "orphans": {
            "claim_subjects_without_entity": [missing_claim_subject],
            "opportunity_subjects_without_entity": [missing_opportunity_subject],
        },
    }


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
        "legacy_inline_stage": 1,
        "legacy_inline_touch": 0,
        "legacy_inline_action": 1,
        "pipeline_relevant_entities": 2,
        "research_only_entities": 1,
        "pipeline_relevant_with_stage": 1,
        "pipeline_relevant_with_touch": 0,
        "pipeline_relevant_with_action": 1,
        "pipeline_relevant_coverage_pct": 50.0,
    }


def test_doctor_counts_frontmatter_crm_fields_and_flags_legacy_inline(tmp_path) -> None:
    root = tmp_path / "vault"
    initialize_vault(root)
    now = datetime.now(UTC)

    frontmatter_entity = EntityRecord(
        type=EntityKind.COMPANY,
        title="Frontmatter CRM Company",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
    )
    fm_metadata = frontmatter_entity.model_dump(mode="json")
    fm_metadata["stage"] = "opportunity"
    fm_metadata["next_action"] = "Send proposal"
    atomic_write_text(
        root,
        f"entities/{frontmatter_entity.id}.md",
        render_frontmatter(fm_metadata, "No inline CRM fields.\n"),
    )

    inline_entity = EntityRecord(
        type=EntityKind.COMPANY,
        title="Inline CRM Company",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
    )
    atomic_write_text(
        root,
        f"entities/{inline_entity.id}.md",
        render_frontmatter(inline_entity.model_dump(mode="json"), "pipeline_stage:: lead\n"),
    )

    crm = cast(dict[str, Any], doctor_report(root)["crm_coverage"])

    assert crm["with_stage"] == 2
    assert crm["legacy_inline_stage"] == 1


def test_validate_vault_scans_people_folder(tmp_path):
    """people/ is a canonical entity folder (person records) — regression test.

    Before the fix, CANONICAL_MODELS lacked "people": validate_vault silently
    skipped every person record and any promotion targeting people/*.md failed
    with "target folder is not canonical".
    """
    from constellation.frontmatter import render_frontmatter
    from constellation.validation import validate_canonical_text, validate_vault
    from constellation.vault import initialize_vault

    root = tmp_path / "vault"
    initialize_vault(root)
    (root / "people").mkdir()
    person = render_frontmatter(
        {
            "schema_version": "0.1",
            "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "type": "person",
            "title": "Fictional Person",
            "status": "active",
            "sensitivity": "internal",
            "created_at": "2026-02-03T00:00:00+00:00",
            "updated_at": "2026-02-03T00:00:00+00:00",
            "aliases": [],
            "source_ids": [],
            "external_ids": {},
            "resolution_state": "unresolved",
        },
        "# Fictional Person\n",
    )
    (root / "people" / "01ARZ3NDEKTSV4RRFFQ69G5FAV.md").write_text(person, encoding="utf-8")

    record = validate_canonical_text(person, "people/01ARZ3NDEKTSV4RRFFQ69G5FAV.md")
    assert str(record.type) == "person"

    report = validate_vault(root)
    assert report["valid"] == 1
    assert report["invalid"] == 0


def test_people_folder_rejects_non_person_entity(tmp_path):
    from constellation.frontmatter import render_frontmatter
    from constellation.validation import CanonicalValidationError, validate_canonical_text

    company = render_frontmatter(
        {
            "schema_version": "0.1",
            "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "type": "company",
            "title": "Wrong Folder Company",
            "status": "active",
            "sensitivity": "internal",
            "created_at": "2026-02-03T00:00:00+00:00",
            "updated_at": "2026-02-03T00:00:00+00:00",
            "aliases": [],
            "source_ids": [],
            "external_ids": {},
            "resolution_state": "unresolved",
        },
        "# Wrong Folder Company\n",
    )

    with pytest.raises(CanonicalValidationError, match="people/ records must have type person"):
        validate_canonical_text(company, "people/company.md")


def _write_evidence_pair(tmp_path, excerpt):
    root = tmp_path / "vault"
    initialize_vault(root)
    source_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    claim_id = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
    source = render_frontmatter(
        {
            "schema_version": "0.1",
            "id": source_id,
            "type": "source-item",
            "title": "Fictional valuation source",
            "status": "active",
            "sensitivity": "internal",
            "created_at": "2026-02-03T00:00:00+00:00",
            "updated_at": "2026-02-03T00:00:00+00:00",
            "source_hash": "1" * 64,
            "original_path": "Inbox/Files/valuation.md",
            "media_type": "text/markdown",
        },
        "Fictional Weave reached a $43B valuation.\n",
    )
    claim_metadata = {
        "schema_version": "0.1",
        "id": claim_id,
        "type": "claim",
        "title": "Fictional valuation claim",
        "status": "review-required",
        "sensitivity": "internal",
        "created_at": "2026-02-03T00:00:00+00:00",
        "updated_at": "2026-02-03T00:00:00+00:00",
        "subject_id": source_id,
        "predicate": "valuation",
        "object_literal": "$43B",
        "source_ids": [source_id],
        "claim_status": "inferred",
        "confidence": 0.7,
        "evidence_excerpt": excerpt,
    }
    (root / "source-items" / f"{source_id}.md").write_text(source, encoding="utf-8")
    claim_path = root / "claims" / f"{claim_id}.md"
    claim_path.write_text(
        render_frontmatter(claim_metadata, "# Fictional valuation claim\n"),
        encoding="utf-8",
    )
    return root, claim_id


def test_validate_vault_rejects_nonverbatim_inferred_evidence(tmp_path):
    from constellation.validation import validate_vault

    root, claim_id = _write_evidence_pair(
        tmp_path, "Fictional Weave reached a 3B valuation."
    )

    rejected = validate_vault(root)

    assert rejected["valid"] == 1
    assert rejected["invalid"] == 1
    assert rejected["errors"] == [
        {
            "path": f"claims/{claim_id}.md",
            "error": "inferred claim evidence excerpt is not verbatim in any cited source",
        }
    ]



def test_validate_vault_accepts_verbatim_inferred_evidence(tmp_path):
    from constellation.validation import validate_vault

    root, _ = _write_evidence_pair(
        tmp_path, "Fictional Weave reached a $43B valuation."
    )

    accepted = validate_vault(root)

    assert accepted["valid"] == 2
    assert accepted["invalid"] == 0
    assert accepted["errors"] == []


def test_validate_vault_rejects_active_exact_body_entity_duplicate(tmp_path):
    from constellation.validation import validate_vault

    root = tmp_path / "vault"
    initialize_vault(root)
    metadata = {
        "schema_version": "0.1",
        "type": "company",
        "status": "active",
        "sensitivity": "internal",
        "created_at": "2026-02-03T00:00:00+00:00",
        "updated_at": "2026-02-03T00:00:00+00:00",
        "aliases": [],
        "source_ids": [],
        "external_ids": {},
        "resolution_state": "unresolved",
    }
    body = "Fictional duplicate dossier content. " * 10
    first = {**metadata, "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "title": "Fictional One"}
    second = {**metadata, "id": "01ARZ3NDEKTSV4RRFFQ69G5FAW", "title": "Fictional Two"}
    (root / "entities/first.md").write_text(render_frontmatter(first, body), encoding="utf-8")
    (root / "entities/second.md").write_text(render_frontmatter(second, body), encoding="utf-8")

    report = validate_vault(root)

    assert report["valid"] == 1
    assert report["invalid"] == 1
    errors = report["errors"]
    assert isinstance(errors, list)
    assert isinstance(errors[0], dict)
    assert str(errors[0]["error"]).startswith("active same-kind entity dossier body duplicates")


def _write_exact_body_entity(root, *, filename, record_id, title, entity_type, body, status="active", resolution_state="unresolved", merged_into=None):
    metadata = {
        "schema_version": "0.1",
        "id": record_id,
        "type": entity_type,
        "title": title,
        "status": status,
        "sensitivity": "internal",
        "created_at": "2026-02-03T00:00:00+00:00",
        "updated_at": "2026-02-03T00:00:00+00:00",
        "aliases": [],
        "source_ids": [],
        "external_ids": {},
        "resolution_state": resolution_state,
    }
    if merged_into is not None:
        metadata["merged_into"] = merged_into
    folder = "people" if entity_type == "person" else "entities"
    directory = root / folder
    directory.mkdir(exist_ok=True)
    (directory / filename).write_text(render_frontmatter(metadata, body), encoding="utf-8")


def test_validate_vault_excludes_merged_exact_body_stub(tmp_path):
    from constellation.validation import validate_vault

    root = tmp_path / "vault"
    initialize_vault(root)
    body = "Fictional duplicate dossier content. " * 10
    keeper_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    _write_exact_body_entity(
        root, filename="keeper.md", record_id=keeper_id, title="Fictional Keeper",
        entity_type="company", body=body,
    )
    _write_exact_body_entity(
        root, filename="merged-stub.md", record_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        title="Fictional Merged Stub", entity_type="company", body=body,
        status="stale", resolution_state="merged", merged_into=keeper_id,
    )

    assert validate_vault(root)["invalid"] == 0


def test_validate_vault_excludes_short_duplicate_boilerplate(tmp_path):
    from constellation.validation import validate_vault

    root = tmp_path / "vault"
    initialize_vault(root)
    body = "Short repeated boilerplate."
    _write_exact_body_entity(
        root, filename="one.md", record_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        title="Fictional One", entity_type="company", body=body,
    )
    _write_exact_body_entity(
        root, filename="two.md", record_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        title="Fictional Two", entity_type="company", body=body,
    )

    assert validate_vault(root)["invalid"] == 0


def test_validate_vault_excludes_exact_body_records_of_different_kinds(tmp_path):
    from constellation.validation import validate_vault

    root = tmp_path / "vault"
    initialize_vault(root)
    body = "Fictional duplicate dossier content. " * 10
    _write_exact_body_entity(
        root, filename="company.md", record_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        title="Fictional Company", entity_type="company", body=body,
    )
    _write_exact_body_entity(
        root, filename="person.md", record_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        title="Fictional Person", entity_type="person", body=body,
    )

    assert validate_vault(root)["invalid"] == 0
