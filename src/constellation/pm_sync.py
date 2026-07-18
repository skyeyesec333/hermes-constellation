"""Opportunity ↔ Project Manager round-trip synchronization.

Separates PM card creation from opportunity staging: staging writes only a
review candidate; promotion creates the canonical opportunity; pm-sync
creates the PM kanban card and writes the reciprocal path back.

Every operation is idempotent via lead_key — rerunning pm-sync on the same
opportunity does not create duplicate cards.
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import parse_frontmatter, render_frontmatter
from .project_manager import (
    ProjectManagerError,
    create_or_update_task,
    ensure_project,
    lead_key,
)
from .storage import atomic_write_text, sha256_file
from .vault import is_initialized


class PmSyncError(RuntimeError):
    """Raised when PM sync fails."""


def _read_opportunity(vault: Path, opportunity_id: str) -> tuple[Path, dict, str]:
    path = vault / "opportunities" / f"{opportunity_id}.md"
    if not path.is_file() or path.is_symlink():
        raise PmSyncError(f"canonical opportunity not found: {opportunity_id}")
    text = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(text)
    if not isinstance(metadata, dict):
        raise PmSyncError(f"opportunity frontmatter is invalid: {opportunity_id}")
    if metadata.get("id") != opportunity_id:
        raise PmSyncError(f"opportunity ID mismatch: {opportunity_id}")
    return path, metadata, body


def pm_sync_plan(vault: Path | str, opportunity_id: str) -> dict[str, object]:
    """Produce a PM sync plan for a promoted opportunity.

    Returns the plan with expected hash — never mutates anything.
    Returns status="synced" if PM card already exists.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise PmSyncError("vault is not initialized")

    path, metadata, _ = _read_opportunity(vault, opportunity_id)
    expected_hash = sha256_file(path)

    # Check if already synced
    existing_kanban = metadata.get("kanban_card_path")
    if existing_kanban:
        kanban_path = vault / str(existing_kanban)
        if kanban_path.is_file():
            return {
                "status": "synced",
                "opportunity_id": opportunity_id,
                "kanban_card_path": str(existing_kanban),
                "expected_sha256": expected_hash,
            }

    # Build plan
    subject_ids = metadata.get("subject_ids", [])
    entity_label = str(subject_ids[0])[:8] if subject_ids else "unknown"
    stage = metadata.get("stage", "test")

    return {
        "status": "plan_ready",
        "opportunity_id": opportunity_id,
        "expected_sha256": expected_hash,
        "proposed": {
            "project_title": "Constellation CRM",
            "task_title": f"opportunity-{entity_label}",
            "task_body": f"Stage: {stage}\nNext action: {metadata.get('next_action', '')}\nEntity: {', '.join(str(s) for s in subject_ids)}",
            "opportunity_path": f"opportunities/{opportunity_id}.md",
        },
    }


def pm_sync_apply(
    vault: Path | str,
    opportunity_id: str,
    *,
    expected_sha256: str,
    dry_run: bool = False,
) -> dict[str, object]:
    """Apply a PM sync plan: create PM card and write path back to opportunity.

    Hash-checked, atomic, idempotent.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise PmSyncError("vault is not initialized")

    path, metadata, body = _read_opportunity(vault, opportunity_id)
    current_hash = sha256_file(path)

    if current_hash != expected_sha256:
        raise PmSyncError(f"hash conflict: opportunity {opportunity_id} changed since plan")

    if dry_run:
        return {"status": "dry_run", "opportunity_id": opportunity_id}

    # Already synced?
    existing = metadata.get("kanban_card_path")
    if existing and (vault / str(existing)).is_file():
        return {"status": "already_synced", "opportunity_id": opportunity_id, "kanban_card_path": str(existing)}

    # Generate stable lead key
    key = lead_key(
        event_date="2026-01-01",
        event_name="constellation-opportunity",
        email=None,
        phone=None,
        name=f"opportunity-{opportunity_id}",
        company=None,
    )

    subject_ids = metadata.get("subject_ids", [])
    entity_label = str(subject_ids[0])[:8] if subject_ids else "unknown"
    stage = str(metadata.get("stage", "test"))

    body_lines = [
        f"Stage: {stage}",
        f"Next action: {metadata.get('next_action', '')}",
        f"Entity IDs: {', '.join(str(s) for s in subject_ids)}",
    ]

    try:
        ensure_project(vault, title="Constellation CRM")
        result = create_or_update_task(
            vault,
            project_title="Constellation CRM",
            title=f"Opportunity: {entity_label}",
            lead_key=key,
            status="open",
            priority="high",
            body_lines=body_lines,
        )
        task_path = result.get("task_path", "")
    except ProjectManagerError as exc:
        return {"status": "pm_failed", "opportunity_id": opportunity_id, "error": str(exc)}

    # Write kanban_card_path back to opportunity
    metadata["kanban_card_path"] = task_path
    new_content = render_frontmatter(metadata, body)
    atomic_write_text(vault, Path("opportunities") / f"{opportunity_id}.md", new_content, expected_hash=expected_sha256)

    return {
        "status": "synced",
        "opportunity_id": opportunity_id,
        "kanban_card_path": task_path,
        "kanban_project": "Constellation CRM",
    }
