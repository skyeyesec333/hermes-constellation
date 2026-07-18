"""Tests for deterministic CRM derivation."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.crm import CrmError, crm_apply, crm_plan, crm_status
from constellation.frontmatter import render_frontmatter
from constellation.models import EntityKind, EntityRecord, Interaction, InteractionType, Sensitivity, generate_ulid
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


def _setup_vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    entity = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.ORGANIZATION,
        title="TestCo",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "entities" / f"{entity.id}.md").write_text(
        render_frontmatter(entity.model_dump(mode="json", exclude_none=True), "# TestCo\n"),
        encoding="utf-8",
    )
    return vault, entity.id


def _add_interaction(vault: Path, entity_id: str) -> str:
    interaction = Interaction(
        id=generate_ulid(),
        title=f"Call with {entity_id[:8]}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        interaction_type=InteractionType.CALL,
        subject_ids=[entity_id],
        participants=[generate_ulid()],
        channel="phone",
        summary="Discussed partnership",
        occurred_at=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "interactions" / f"{interaction.id}.md").write_text(
        render_frontmatter(interaction.model_dump(mode="json", exclude_none=True), "# Interaction\n"),
        encoding="utf-8",
    )
    return interaction.id


def test_plan_derives_stage_from_interactions(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    _add_interaction(vault, entity_id)

    plan = crm_plan(vault, entity_id=entity_id)
    assert len(plan) == 1
    assert plan[0]["proposed"]["stage"] == "engaged"


def test_plan_noop_when_stage_matches(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    # Entity has no stage, no interactions — defaults to "research-only"
    plan = crm_plan(vault, entity_id=entity_id)
    assert len(plan) == 1
    assert plan[0]["proposed"]["stage"] == "research-only"


def test_plan_derives_last_touch_from_interaction(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    _add_interaction(vault, entity_id)

    plan = crm_plan(vault, entity_id=entity_id)
    assert plan[0]["proposed"]["last_touch"]


def test_apply_updates_entity_with_hash_check(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    _add_interaction(vault, entity_id)

    plan = crm_plan(vault, entity_id=entity_id)
    proposal = plan[0]

    result = crm_apply(
        vault, entity_id,
        expected_sha256=str(proposal["expected_sha256"]),
        changes=proposal["proposed"],
    )
    assert result["status"] == "applied"


def test_apply_rejects_hash_mismatch(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)

    with pytest.raises(CrmError, match="hash conflict"):
        crm_apply(vault, entity_id, expected_sha256="0" * 64, changes={"stage": "engaged"})


def test_apply_dry_run_does_not_write(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    _add_interaction(vault, entity_id)

    plan = crm_plan(vault, entity_id=entity_id)
    proposal = plan[0]

    result = crm_apply(
        vault, entity_id,
        expected_sha256=str(proposal["expected_sha256"]),
        changes=proposal["proposed"],
        dry_run=True,
    )
    assert result["status"] == "dry_run"


def test_status_reports_coverage(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    _add_interaction(vault, entity_id)

    plan = crm_plan(vault, entity_id=entity_id)
    proposal = plan[0]
    crm_apply(vault, entity_id, expected_sha256=str(proposal["expected_sha256"]), changes=proposal["proposed"])

    status = crm_status(vault)
    assert status["total"] == 1
    assert status["with_stage"] == 1


def test_crm_cli_args() -> None:
    from constellation.cli import build_parser

    values = vars(
        build_parser().parse_args([
            "crm", "plan", "/tmp/vault",
            "--entity-id", "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        ])
    )
    assert values["crm_action"] == "plan"
    assert values["entity_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
