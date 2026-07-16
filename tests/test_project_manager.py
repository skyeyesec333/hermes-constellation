from datetime import date

import pytest
import yaml

from constellation.project_manager import (
    ProjectManagerError,
    create_or_update_task,
    ensure_project,
    lead_key,
)
from constellation.vault import initialize_vault


def test_ensure_project_creates_pm_project_and_tasks_dir(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)

    result = ensure_project(vault, title="InfoComm Asia 2026 Leads")

    project_path = vault / "Projects" / "InfoComm Asia 2026 Leads.md"
    tasks_dir = vault / "Projects" / "InfoComm Asia 2026 Leads_tasks"
    assert result["status"] == "created"
    assert project_path.is_file()
    assert tasks_dir.is_dir()
    meta = yaml.safe_load(project_path.read_text(encoding="utf-8").split("---", 2)[1])
    assert meta["pm-project"] is True
    assert meta["title"] == "InfoComm Asia 2026 Leads"
    assert meta["taskIds"] == []
    assert isinstance(meta["id"], str) and len(meta["id"]) >= 12


def test_create_task_is_idempotent_and_registers_task_id(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    project = ensure_project(vault, title="InfoComm Asia 2026 Leads")

    first = create_or_update_task(
        vault,
        project_title="InfoComm Asia 2026 Leads",
        title="Ada Example (Example Co) — InfoComm follow-up",
        lead_key="a1b2c3d4e5f60718",
        status="open",
        priority="high",
        body_lines=[
            "Status: open · card captured",
            "Next: review draft",
            "Links: none yet",
        ],
        start=date(2026, 7, 21),
    )
    second = create_or_update_task(
        vault,
        project_title="InfoComm Asia 2026 Leads",
        title="Ada Example (Example Co) — InfoComm follow-up",
        lead_key="a1b2c3d4e5f60718",
        status="in-progress",
        priority="high",
        body_lines=[
            "Status: in-progress · packet ready",
            "Next: approve WhatsApp draft",
            "Links: [[person-ada-example]]",
        ],
        start=date(2026, 7, 21),
    )

    assert first["status"] == "created"
    assert second["status"] == "updated"
    assert first["task_id"] == second["task_id"]
    project_meta = yaml.safe_load(
        (vault / "Projects" / "InfoComm Asia 2026 Leads.md")
        .read_text(encoding="utf-8")
        .split("---", 2)[1]
    )
    assert project_meta["taskIds"] == [first["task_id"]]
    task_text = (vault / first["task_path"]).read_text(encoding="utf-8")
    assert "packet ready" in task_text
    assert "status: in-progress" in task_text
    mapping = vault / ".constellation" / "leads" / "a1b2c3d4e5f60718.json"
    assert mapping.is_file()
    assert project["project_id"] == project_meta["id"]


def test_lead_key_is_stable_and_path_escape_rejected(tmp_path):
    assert lead_key(
            event_date="2026-07-21",
            event_name="InfoComm Asia",
            email="ada@" + "mail.example.test",
            phone=None,
            name="Ada Example",
            company="Example Co",
        ) == lead_key(
            event_date="2026-07-21",
            event_name="InfoComm Asia",
            email="ada@" + "mail.example.test",
            phone="+" + "1" + ("0" * 10),
            name="Different",
            company="Other",
        )

    vault = tmp_path / "vault"
    initialize_vault(vault)
    with pytest.raises(ProjectManagerError):
        ensure_project(vault, title="../escape")
