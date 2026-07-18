"""Deterministic CRM derivation from canonical Constellation records.

Reads entities, interactions, opportunities, and claims; derives stage,
next_action, and last_touch fields.  Never invents data — every proposal is
traceable to explicit linked records.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from .frontmatter import parse_frontmatter, render_frontmatter
from .storage import atomic_write_text, sha256_file
from .vault import is_initialized


class CrmError(RuntimeError):
    """Raised when CRM operations fail."""


# ── Evidence extraction ────────────────────────────────────────────────


def _read_entity(vault: Path, entity_id: str) -> tuple[Path, dict, str]:
    path = vault / "entities" / f"{entity_id}.md"
    if not path.is_file() or path.is_symlink():
        raise CrmError(f"entity not found: {entity_id}")
    text = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(text)
    if not isinstance(metadata, dict):
        raise CrmError(f"entity frontmatter is invalid: {entity_id}")
    return path, metadata, body


def _read_entity_metadata(vault: Path, entity_id: str) -> dict:
    return _read_entity(vault, entity_id)[1]


def _interactions_for_entity(vault: Path, entity_id: str) -> list[dict]:
    """Return all interactions referencing this entity, newest first."""
    interactions_dir = vault / "interactions"
    if not interactions_dir.is_dir():
        return []
    results: list[dict] = []
    for path in sorted(interactions_dir.glob("*.md"), reverse=True):
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                continue
            subject_ids = metadata.get("subject_ids", [])
            if entity_id in (subject_ids if isinstance(subject_ids, list) else []):
                results.append(metadata)
        except Exception:
            continue
    return results


def _opportunities_for_entity(vault: Path, entity_id: str) -> list[dict]:
    """Return all opportunities referencing this entity."""
    opp_dir = vault / "opportunities"
    if not opp_dir.is_dir():
        return []
    results: list[dict] = []
    for path in sorted(opp_dir.glob("*.md"), reverse=True):
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                continue
            subject_ids = metadata.get("subject_ids", [])
            if entity_id in (subject_ids if isinstance(subject_ids, list) else []):
                results.append(metadata)
        except Exception:
            continue
    return results


def _classifications_for_entity(vault: Path, entity_id: str) -> list[dict]:
    """Return all classifications for this entity."""
    cls_dir = vault / "classifications"
    if not cls_dir.is_dir():
        return []
    results: list[dict] = []
    for path in sorted(cls_dir.glob("*.md"), reverse=True):
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                continue
            if metadata.get("entity_id") == entity_id:
                results.append(metadata)
        except Exception:
            continue
    return results


# ── Derivation ──────────────────────────────────────────────────────────


def _derive_stage(entity_id: str, opportunities: list[dict], classifications: list[dict], interactions: list[dict]) -> str:
    """Derive pipeline stage from linked records only."""
    if classifications:
        latest = classifications[0]
        category = latest.get("category", "")
        if latest.get("operator_reviewed"):
            return f"classified-{category}"
        return f"classification-review-{category}"

    if opportunities:
        latest = opportunities[0]
        stage = latest.get("stage", "")
        if stage:
            return str(stage)
        return "opportunity-identified"

    if interactions:
        return "engaged"

    return "research-only"


def _derive_next_action(opportunities: list[dict], interactions: list[dict]) -> str | None:
    """Derive next action from linked records only."""
    if opportunities:
        latest = opportunities[0]
        action = latest.get("next_action")
        if action:
            return str(action)
        return None

    if interactions:
        # Suggest follow-up after most recent interaction
        latest = interactions[0]
        occurred = latest.get("occurred_at", "")
        return f"follow-up after {occurred[:10]}" if occurred else "schedule-next-touch"

    return None


def _derive_last_touch(interactions: list[dict]) -> str | None:
    """Derive last_touch only from actual Interaction timestamps."""
    if interactions:
        latest = interactions[0]
        occurred = latest.get("occurred_at")
        if occurred:
            return str(occurred)
    return None


# ── Plan / Apply / Status ───────────────────────────────────────────────


def crm_plan(vault: Path | str, entity_id: str | None = None) -> list[dict[str, object]]:
    """Produce a deterministic CRM plan for one or all entities.

    Returns list of proposals, each with entity path, current/expected hashes,
    proposed fields, evidence record IDs, reason.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise CrmError("vault is not initialized")

    entities_dir = vault / "entities"
    if not entities_dir.is_dir():
        return []

    targets = (
        [entity_id]
        if entity_id
        else sorted(p.stem for p in entities_dir.glob("*.md") if not p.is_symlink())
    )

    proposals: list[dict[str, object]] = []
    for eid in targets:
        try:
            path, metadata, _ = _read_entity(vault, eid)
        except CrmError:
            continue

        interactions = _interactions_for_entity(vault, eid)
        opportunities = _opportunities_for_entity(vault, eid)
        classifications = _classifications_for_entity(vault, eid)

        current_stage = metadata.get("stage", "")
        current_next_action = metadata.get("next_action")
        current_last_touch = metadata.get("last_touch")

        derived_stage = _derive_stage(eid, opportunities, classifications, interactions)
        derived_next_action = _derive_next_action(opportunities, interactions)
        derived_last_touch = _derive_last_touch(interactions)

        changes: dict[str, object] = {}
        evidence: list[str] = []

        if derived_stage and derived_stage != str(current_stage):
            changes["stage"] = derived_stage
            evidence.append("derived from opportunities, classifications, interactions")

        if derived_next_action and derived_next_action != str(current_next_action or ""):
            changes["next_action"] = derived_next_action
            evidence.append("derived from opportunities, interactions")

        if derived_last_touch and derived_last_touch != str(current_last_touch or ""):
            changes["last_touch"] = derived_last_touch
            evidence.append("derived from interaction timestamp")

        if not changes:
            continue

        expected_hash = sha256_file(path)
        proposals.append({
            "entity_id": eid,
            "entity_path": str(path.relative_to(vault)),
            "expected_sha256": expected_hash,
            "current_stage": current_stage,
            "current_next_action": current_next_action,
            "current_last_touch": current_last_touch,
            "proposed": changes,
            "evidence_ids": evidence,
            "reason": f"Deterministic CRM derivation from {len(interactions)} interactions, {len(opportunities)} opportunities, {len(classifications)} classifications",
        })

    return proposals


def crm_apply(
    vault: Path | str,
    entity_id: str,
    *,
    expected_sha256: str,
    changes: dict[str, object],
    dry_run: bool = False,
) -> dict[str, object]:
    """Apply CRM changes to one entity with hash-checked atomic write.

    Never writes if the current hash doesn't match expected_sha256.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise CrmError("vault is not initialized")

    path, metadata, body = _read_entity(vault, entity_id)
    current_hash = sha256_file(path)

    if current_hash != expected_sha256:
        raise CrmError(f"hash conflict: entity {entity_id} changed since plan was generated")

    # Apply changes to metadata only
    updated_metadata = deepcopy(metadata)
    if "stage" in changes:
        updated_metadata["stage"] = changes["stage"]
    if "next_action" in changes:
        updated_metadata["next_action"] = changes["next_action"]
    if "last_touch" in changes:
        updated_metadata["last_touch"] = changes["last_touch"]
    updated_metadata["updated_at"] = datetime.now(UTC).isoformat()

    new_content = render_frontmatter(updated_metadata, body)

    if dry_run:
        return {
            "status": "dry_run",
            "entity_id": entity_id,
            "changes": changes,
        }

    atomic_write_text(vault, Path("entities") / f"{entity_id}.md", new_content, expected_hash=expected_sha256)

    return {
        "status": "applied",
        "entity_id": entity_id,
        "changes": changes,
        "new_sha256": sha256_file(path),
    }


def crm_status(vault: Path | str) -> dict[str, object]:
    """Report CRM coverage across all entities."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise CrmError("vault is not initialized")

    entities_dir = vault / "entities"
    if not entities_dir.is_dir():
        return {"total": 0, "with_stage": 0, "with_next_action": 0, "with_last_touch": 0}

    total = 0
    with_stage = 0
    with_next_action = 0
    with_last_touch = 0

    for path in entities_dir.glob("*.md"):
        if path.is_symlink():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                continue
            total += 1
            if metadata.get("stage"):
                with_stage += 1
            if metadata.get("next_action"):
                with_next_action += 1
            if metadata.get("last_touch"):
                with_last_touch += 1
        except Exception:
            continue

    return {
        "total": total,
        "with_stage": with_stage,
        "with_next_action": with_next_action,
        "with_last_touch": with_last_touch,
    }
