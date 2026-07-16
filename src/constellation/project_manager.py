"""Obsidian Project Manager project/task writer for CRM kanban + gantt notes.

Project Manager stores UI state as markdown notes under Projects/ with
pm-project / pm-task frontmatter. This module writes those notes safely so
Constellation can put conference leads on Bryan's existing CRM surface.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from .storage import atomic_write_text, safe_relative_path
from .vault import is_initialized

_SAFE_TITLE = re.compile(r"^[^/\\]+$")
_SLUG_SAFE = re.compile(r"[^a-z0-9]+")


class ProjectManagerError(RuntimeError):
    """Raised when Project Manager notes cannot be written safely."""


def _now() -> datetime:
    return datetime.now(UTC)


def _pm_id() -> str:
    # Match observed PM ids: lowercase alphanumeric ~16 chars.
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


def _slug(title: str) -> str:
    slug = _SLUG_SAFE.sub("-", title.strip().casefold()).strip("-")
    return slug[:80] or "task"


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ProjectManagerError("project manager note is missing frontmatter")
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        raise ProjectManagerError("project manager note frontmatter is malformed")
    meta = yaml.safe_load(parts[1]) or {}
    if not isinstance(meta, dict):
        raise ProjectManagerError("project manager frontmatter must be a mapping")
    return meta, parts[2]


def _render_note(meta: dict[str, Any], body: str) -> str:
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    body = body.rstrip() + "\n"
    return f"---\n{front}\n---\n\n{body}"


def lead_key(
    *,
    event_date: str,
    event_name: str,
    email: str | None,
    phone: str | None,
    name: str | None,
    company: str | None,
) -> str:
    """Stable lead identity for idempotent PM task upserts."""
    import hashlib

    if email:
        identity = f"email:{email.strip().casefold()}"
    elif phone:
        identity = f"phone:{re.sub(r'[^0-9+]', '', phone)}"
    else:
        identity = (
            f"name:{(name or '').strip().casefold()}|company:{(company or '').strip().casefold()}"
        )
    raw = f"{event_date.strip()}|{event_name.strip().casefold()}|{identity}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def ensure_project(root: Path | str, *, title: str, color: str = "#8b72be") -> dict[str, str]:
    """Create or return an Obsidian Project Manager project note + tasks folder."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise ProjectManagerError("vault is not initialized")
    title = title.strip()
    if not title or not _SAFE_TITLE.fullmatch(title) or title in {".", ".."}:
        raise ProjectManagerError("project title is unsafe")
    if title.startswith("."):
        raise ProjectManagerError("project title is unsafe")

    project_relative = Path("Projects") / f"{title}.md"
    tasks_relative = Path("Projects") / f"{title}_tasks"
    project_path = safe_relative_path(vault, project_relative)
    tasks_path = safe_relative_path(vault, tasks_relative)
    tasks_path.mkdir(parents=True, exist_ok=True)

    if project_path.exists():
        if project_path.is_symlink() or not project_path.is_file():
            raise ProjectManagerError("project note is unsafe")
        meta, body = _split_frontmatter(project_path.read_text(encoding="utf-8"))
        if meta.get("pm-project") is not True:
            raise ProjectManagerError("existing project note is not a Project Manager project")
        project_id = str(meta.get("id") or "")
        if not project_id:
            raise ProjectManagerError("existing project is missing id")
        return {
            "status": "existing",
            "project_id": project_id,
            "project_path": project_relative.as_posix(),
            "tasks_dir": tasks_relative.as_posix(),
            "title": str(meta.get("title") or title),
        }

    now = _now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    project_id = _pm_id()
    meta = {
        "pm-project": True,
        "id": project_id,
        "title": title,
        "description": "",
        "color": color,
        "icon": "📋",
        "taskIds": [],
        "customFields": [],
        "teamMembers": [],
        "savedViews": [],
        "createdAt": now,
        "updatedAt": now,
    }
    body = f"# 📋 {title}\n"
    atomic_write_text(vault, project_relative, _render_note(meta, body))
    return {
        "status": "created",
        "project_id": project_id,
        "project_path": project_relative.as_posix(),
        "tasks_dir": tasks_relative.as_posix(),
        "title": title,
    }


def create_or_update_task(
    root: Path | str,
    *,
    project_title: str,
    title: str,
    lead_key: str,
    status: str = "open",
    priority: str = "high",
    body_lines: list[str] | None = None,
    start: date | None = None,
    due: date | None = None,
) -> dict[str, str]:
    """Create or update a PM task and keep project.taskIds in sync."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise ProjectManagerError("vault is not initialized")
    if not re.fullmatch(r"[0-9a-f]{16}", lead_key):
        raise ProjectManagerError("lead_key must be 16 lowercase hex chars")
    if status not in {"open", "in-progress", "blocked", "done"}:
        raise ProjectManagerError("unsupported task status")
    if priority not in {"low", "medium", "high"}:
        raise ProjectManagerError("unsupported task priority")
    title = title.strip()
    if not title:
        raise ProjectManagerError("task title cannot be empty")

    project = ensure_project(vault, title=project_title)
    project_relative = Path(project["project_path"])
    project_path = safe_relative_path(vault, project_relative)
    project_meta, project_body = _split_frontmatter(project_path.read_text(encoding="utf-8"))
    project_id = str(project_meta["id"])
    task_ids = list(project_meta.get("taskIds") or [])
    if not isinstance(task_ids, list):
        raise ProjectManagerError("project taskIds must be a list")

    mapping_relative = Path(".constellation/leads") / f"{lead_key}.json"
    mapping_path = safe_relative_path(vault, mapping_relative)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)

    existing_task_id: str | None = None
    existing_task_rel: Path | None = None
    if mapping_path.exists():
        if mapping_path.is_symlink() or not mapping_path.is_file():
            raise ProjectManagerError("lead mapping is unsafe")
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        existing_task_id = str(mapping.get("task_id") or "") or None
        rel = mapping.get("task_path")
        if rel:
            existing_task_rel = Path(str(rel))

    now = _now()
    now_stamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    start_s = (start or now.date()).isoformat()
    due_s = due.isoformat() if due else ""
    body = "\n".join(body_lines or ["Status: open", "Next: review", "Links:"])
    body = body.rstrip() + f"\n\nProject: [[{project_title}|{project_title}]]\n"

    if existing_task_id and existing_task_rel is not None:
        task_path = safe_relative_path(vault, existing_task_rel)
        if not task_path.is_file() or task_path.is_symlink():
            raise ProjectManagerError("mapped task note is missing or unsafe")
        task_meta, _old_body = _split_frontmatter(task_path.read_text(encoding="utf-8"))
        if task_meta.get("pm-task") is not True:
            raise ProjectManagerError("mapped note is not a Project Manager task")
        task_meta.update(
            {
                "title": title,
                "status": status,
                "priority": priority,
                "start": start_s,
                "due": due_s,
                "updatedAt": now_stamp,
            }
        )
        atomic_write_text(vault, existing_task_rel, _render_note(task_meta, body))
        if existing_task_id not in task_ids:
            task_ids.append(existing_task_id)
            project_meta["taskIds"] = task_ids
            project_meta["updatedAt"] = now_stamp
            atomic_write_text(vault, project_relative, _render_note(project_meta, project_body))
        mapping_payload = {
            "lead_key": lead_key,
            "project_id": project_id,
            "project_title": project_title,
            "task_id": existing_task_id,
            "task_path": existing_task_rel.as_posix(),
            "status": status,
            "updated_at": now_stamp,
        }
        atomic_write_text(
            vault, mapping_relative, json.dumps(mapping_payload, indent=2, sort_keys=True) + "\n"
        )
        return {
            "status": "updated",
            "project_id": project_id,
            "task_id": existing_task_id,
            "task_path": existing_task_rel.as_posix(),
            "project_path": project_relative.as_posix(),
        }

    task_id = _pm_id()
    slug = _slug(title)
    task_relative = Path("Projects") / f"{project_title}_tasks" / f"{slug}.md"
    # Avoid clobbering a different task with same title slug.
    candidate = safe_relative_path(vault, task_relative)
    if candidate.exists():
        task_relative = Path("Projects") / f"{project_title}_tasks" / f"{slug}-{task_id[:6]}.md"

    task_meta = {
        "pm-task": True,
        "projectId": project_id,
        "parentId": None,
        "id": task_id,
        "title": title,
        "type": "task",
        "status": status,
        "priority": priority,
        "start": start_s,
        "due": due_s,
        "progress": 0,
        "assignees": [],
        "tags": ["conference", "lead", "follow-up"],
        "subtaskIds": [],
        "dependencies": [],
        "createdAt": now_stamp,
        "updatedAt": now_stamp,
    }
    atomic_write_text(vault, task_relative, _render_note(task_meta, body))
    if task_id not in task_ids:
        task_ids.append(task_id)
    project_meta["taskIds"] = task_ids
    project_meta["updatedAt"] = now_stamp
    atomic_write_text(vault, project_relative, _render_note(project_meta, project_body))
    mapping_payload = {
        "lead_key": lead_key,
        "project_id": project_id,
        "project_title": project_title,
        "task_id": task_id,
        "task_path": task_relative.as_posix(),
        "status": status,
        "updated_at": now_stamp,
    }
    atomic_write_text(
        vault, mapping_relative, json.dumps(mapping_payload, indent=2, sort_keys=True) + "\n"
    )
    return {
        "status": "created",
        "project_id": project_id,
        "task_id": task_id,
        "task_path": task_relative.as_posix(),
        "project_path": project_relative.as_posix(),
    }
