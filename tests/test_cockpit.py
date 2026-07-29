"""Tests for the Obsidian cockpit review surface."""

from datetime import UTC, datetime
from pathlib import Path

from constellation.cockpit import cockpit_apply, cockpit_plan, cockpit_status
from constellation.frontmatter import render_frontmatter
from constellation.models import EntityKind, EntityRecord, Sensitivity, generate_ulid
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _setup(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="TestCo",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    (vault / "entities" / f"{entity.id}.md").write_text(
        render_frontmatter(entity.model_dump(mode="json", exclude_none=True), "# TestCo\n"),
        encoding="utf-8",
    )
    return vault


def test_apply_renders_lint_findings_section(tmp_path: Path) -> None:
    vault = _setup(tmp_path)
    # Anchorless legacy claim → lint finding must surface in the cockpit
    claim_id = generate_ulid()
    (vault / "claims" / f"{claim_id}.md").write_text(
        render_frontmatter({
            "schema_version": "0.1", "id": claim_id, "type": "claim",
            "title": "Anchorless", "status": "active", "sensitivity": "internal",
            "subject_id": generate_ulid(), "predicate": "competes_in",
            "object_literal": "logistics", "source_ids": [],
            "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
        }, "# Anchorless\n"),
        encoding="utf-8",
    )

    result = cockpit_apply(vault)

    assert result["status"] == "applied"
    content = (vault / "COCKPIT.md").read_text(encoding="utf-8")
    assert "Record Health" in content
    assert "claim_without_sources" in content
    assert "high" in content


def test_apply_renders_feeder_circuit_state(tmp_path: Path) -> None:
    import json

    from constellation.storage import atomic_write_text

    vault = _setup(tmp_path)
    atomic_write_text(
        vault,
        Path(".constellation/feeder-health.json"),
        json.dumps({"gdelt": {"consecutive_failures": 3, "last_status": "failed"}}) + "\n",
    )

    cockpit_apply(vault)

    content = (vault / "COCKPIT.md").read_text(encoding="utf-8")
    assert "Feeder Circuits" in content
    assert "gdelt" in content
    assert "OPEN" in content


def test_apply_renders_semantic_index_state(tmp_path: Path) -> None:
    vault = _setup(tmp_path)

    cockpit_apply(vault)

    content = (vault / "COCKPIT.md").read_text(encoding="utf-8")
    assert "Semantic Index" in content
    assert "missing" in content  # no index built in this vault


def test_apply_keeps_dataview_sections(tmp_path: Path) -> None:
    vault = _setup(tmp_path)

    cockpit_apply(vault)

    content = (vault / "COCKPIT.md").read_text(encoding="utf-8")
    assert "```dataview" in content
    assert "Candidate Queue" in content


def test_plan_and_status_unchanged_contract(tmp_path: Path) -> None:
    vault = _setup(tmp_path)

    plan = cockpit_plan(vault)
    assert plan["status"] == "plan_ready"
    cockpit_apply(vault)
    status = cockpit_status(vault)
    assert status["cockpit_exists"] is True


def test_apply_never_touches_owner_home_md(tmp_path: Path) -> None:
    vault = _setup(tmp_path)
    (vault / "HOME.md").write_text("# My hand-maintained dashboard\n", encoding="utf-8")

    result = cockpit_apply(vault)

    assert result["status"] == "applied"
    assert (vault / "HOME.md").read_text(encoding="utf-8") == "# My hand-maintained dashboard\n"
    assert (vault / "COCKPIT.md").is_file()
    assert "Record Health" in (vault / "COCKPIT.md").read_text(encoding="utf-8")


def test_clean_vault_reports_zero_findings_in_cockpit(tmp_path: Path) -> None:
    vault = _setup(tmp_path)

    cockpit_apply(vault)

    content = (vault / "COCKPIT.md").read_text(encoding="utf-8")
    assert "0 findings" in content
