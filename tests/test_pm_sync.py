"""Tests for one-way canonical Opportunity → PM card synchronization."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.models import (
    EntityKind,
    EntityRecord,
    Opportunity,
    OpportunityStage,
    Sensitivity,
    generate_ulid,
)
from constellation.pm_sync import PmSyncError, pm_sync_apply, pm_sync_plan
from constellation.prep import compile_prep
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


def _add_opportunity(vault: Path, entity_id: str) -> str:
    opp = Opportunity(
        id=generate_ulid(),
        title=f"opportunity-{entity_id[:8]}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        subject_ids=[entity_id],
        stage=OpportunityStage.TEST,
        probability=0.5,
        next_action="Schedule call",
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "opportunities" / f"{opp.id}.md").write_text(
        render_frontmatter(opp.model_dump(mode="json", exclude_none=True), "# Opp\n"),
        encoding="utf-8",
    )
    return opp.id


def test_plan_produces_sync_plan(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    opp_id = _add_opportunity(vault, entity_id)

    plan = pm_sync_plan(vault, opp_id)
    assert plan["status"] == "plan_ready"
    assert plan["opportunity_id"] == opp_id
    assert "expected_sha256" in plan
    proposed = plan["proposed"]
    assert isinstance(proposed, dict)
    assert proposed["project_title"] == "Constellation CRM"
    task_body = str(proposed["task_body"])
    assert f"[[opportunities/{opp_id}.md]]" in task_body
    assert f"[[entities/{entity_id}.md]]" in task_body
    assert "Inbound PM → canonical sync: unsupported in this beta." in task_body


def test_plan_recognizes_already_synced(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    opp_id = _add_opportunity(vault, entity_id)

    plan = pm_sync_plan(vault, opp_id)
    pm_sync_apply(vault, opp_id, expected_sha256=str(plan["expected_sha256"]))

    # Second plan should show synced
    plan2 = pm_sync_plan(vault, opp_id)
    assert plan2["status"] == "synced"


def test_apply_writes_reciprocal_opportunity_and_entity_links(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    opp_id = _add_opportunity(vault, entity_id)
    plan = pm_sync_plan(vault, opp_id)

    result = pm_sync_apply(vault, opp_id, expected_sha256=str(plan["expected_sha256"]))

    opportunity_text = (vault / "opportunities" / f"{opp_id}.md").read_text(encoding="utf-8")
    opportunity_metadata, _ = parse_frontmatter(opportunity_text)
    assert opportunity_metadata["kanban_card_path"] == result["kanban_card_path"]
    task_text = (vault / str(result["kanban_card_path"])).read_text(encoding="utf-8")
    assert f"opportunities/{opp_id}.md" in task_text
    assert f"entities/{entity_id}.md" in task_text
    assert "Inbound PM → canonical sync: unsupported in this beta." in task_text
    prep = compile_prep(vault, entity_id)
    assert str(result["kanban_card_path"]) in prep
    assert "PM → canonical synchronization: unsupported in this beta." in prep


def test_apply_rejects_hash_mismatch(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    opp_id = _add_opportunity(vault, entity_id)

    with pytest.raises(PmSyncError, match="hash conflict"):
        pm_sync_apply(vault, opp_id, expected_sha256="0" * 64)


def test_apply_dry_run(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    opp_id = _add_opportunity(vault, entity_id)

    plan = pm_sync_plan(vault, opp_id)
    result = pm_sync_apply(
        vault, opp_id,
        expected_sha256=str(plan["expected_sha256"]),
        dry_run=True,
    )
    assert result["status"] == "dry_run"


def test_missing_opportunity_rejected(tmp_path: Path) -> None:
    vault, _ = _setup_vault(tmp_path)
    with pytest.raises(PmSyncError, match="canonical opportunity"):
        pm_sync_plan(vault, "nonexistent")


def test_pm_sync_cli_args() -> None:
    from constellation.cli import build_parser

    values = vars(
        build_parser().parse_args([
            "pm-sync", "plan", "/tmp/vault",
            "--opportunity-id", "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        ])
    )
    assert values["pm_sync_action"] == "plan"
    assert values["opportunity_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
