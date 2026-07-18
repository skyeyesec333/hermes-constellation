"""Meeting prep packet compiler — one-command operator brief from vault data.

Phase 8: constellation prep <entity-id> produces a one-page briefing from
scattered vault records — entity, interactions, claims, decisions, opportunities,
CRM state, and a recommended approach.
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import parse_frontmatter
from .vault import is_initialized


class PrepError(RuntimeError):
    """Raised when a prep packet cannot be compiled."""


def _entity_snapshot(vault: Path, entity_id: str) -> dict[str, object] | None:
    """Read the canonical entity record, searching by filename then frontmatter id."""
    # Try direct filename match first
    path = vault / "entities" / f"{entity_id}.md"
    if not path.is_file():
        # Search all entities by frontmatter id field
        for candidate in sorted((vault / "entities").glob("*.md")):
            try:
                fm, body = parse_frontmatter(candidate.read_text(encoding="utf-8"))
                if isinstance(fm, dict) and str(fm.get("id", "")) == entity_id:
                    path = candidate
                    break
            except Exception:
                continue
    if not path.is_file():
        return None
    try:
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        if not isinstance(fm, dict):
            return None
    except Exception:
        return None
    return {
        "id": str(fm.get("id", "")),
        "title": str(fm.get("title", "")),
        "kind": str(fm.get("type", "unknown")),
        "status": str(fm.get("status", "unknown")),
        "body": str(body).strip(),
    }


def _linked_records(
    vault: Path, entity_id: str
) -> dict[str, list[dict[str, object]]]:
    """Find all claims, interactions, decisions, and opportunities referencing this entity."""

    def _scan(folder: str, id_key: str) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        base = vault / folder
        if not base.is_dir():
            return results
        for path in sorted(base.glob("*.md")):
            try:
                fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
                if not isinstance(fm, dict):
                    continue
            except Exception:
                continue
            # Check single subject_id (claims) or list subject_ids
            ids = []
            if id_key in fm:
                val = fm[id_key]
                if isinstance(val, str):
                    ids = [val]
                elif isinstance(val, list):
                    ids = [str(v) for v in val]
            if entity_id in ids:
                results.append({
                    "id": str(fm.get("id", path.stem)),
                    "title": str(fm.get("title", "")),
                    "summary": str(body).strip()[:300],
                    "date": str(
                        fm.get("observed_at")
                        or fm.get("occurred_at")
                        or fm.get("decided_at")
                        or fm.get("created_at", "")
                    )[:10],
                })
        return results

    return {
        "claims": _scan("claims", "subject_id"),
        "interactions": _scan("interactions", "subject_ids"),
        "decisions": _scan("decisions", "subject_ids"),
        "opportunities": _scan("opportunities", "subject_ids"),
    }


def _crm_context(vault: Path, entity_id: str) -> dict[str, str]:
    """Extract CRM metadata from an entity's body (Dataview inline fields)."""
    path = vault / "entities" / f"{entity_id}.md"
    if not path.is_file():
        return {}
    try:
        _, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        if not isinstance(body, str):
            return {}
    except Exception:
        return {}
    crm: dict[str, str] = {}
    for line in body.splitlines():
        if "::" in line:
            key, _, value = line.partition("::")
            key = key.strip()
            value = value.strip()
            if key and value:
                crm[key] = value
    return crm


def compile_prep(root: Path | str, entity_id: str) -> str:
    """Compile a one-page operator brief for the given canonical entity.

    Returns a markdown string suitable for display in a terminal or Obsidian note.
    """
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise PrepError("vault is not initialized")

    entity = _entity_snapshot(vault, entity_id)
    if entity is None:
        raise PrepError(f"entity not found: {entity_id}")

    linked = _linked_records(vault, entity_id)
    crm = _crm_context(vault, entity_id)

    lines: list[str] = []
    title = str(entity["title"])
    kind = str(entity["kind"])

    # ── Header ──
    lines.append(f"# Prep Brief: {title}")
    lines.append("")
    lines.append(f"**Entity:** {title}  ")
    lines.append(f"**Kind:** {kind}  ")

    if crm.get("pipeline_stage"):
        lines.append(f"**Stage:** {crm['pipeline_stage']}  ")
    if crm.get("last_touch"):
        lines.append(f"**Last touch:** {crm['last_touch']}  ")
    if crm.get("priority"):
        lines.append(f"**Priority:** {crm['priority']}  ")
    lines.append("")

    # ── CRM context ──
    if crm.get("next_action"):
        lines.append("## Next Action")
        lines.append("")
        lines.append(crm["next_action"])
        lines.append("")
    if crm.get("open_questions"):
        lines.append("## Open Questions")
        lines.append("")
        lines.append(crm["open_questions"])
        lines.append("")

    # ── Recent interactions ──
    interactions = linked.get("interactions", [])
    if interactions:
        lines.append("## Recent Interactions")
        lines.append("")
        for ix in interactions[-5:]:
            lines.append(f"- **{ix['date']}** — {ix['summary'][:200]}")
        lines.append("")

    # ── Active claims ──
    claims = linked.get("claims", [])
    if claims:
        lines.append("## Active Claims")
        lines.append("")
        for c in claims[-10:]:
            title = str(c.get("title", ""))
            lines.append(f"- {title}")
        lines.append("")

    # ── Decisions ──
    decisions = linked.get("decisions", [])
    if decisions:
        lines.append("## Decisions")
        lines.append("")
        for d in decisions[-5:]:
            lines.append(f"- **{d['date']}** — {d['summary'][:200]}")
        lines.append("")

    # ── Opportunities ──
    opportunities = linked.get("opportunities", [])
    if opportunities:
        lines.append("## Opportunities")
        lines.append("")
        for o in opportunities[-5:]:
            lines.append(f"- {o['title']}")
        lines.append("")

    # ── Entity background ──
    body = str(entity.get("body", "")).strip()
    if body:
        lines.append("## Background")
        lines.append("")
        # Strip Dataview inline fields from background display
        clean_body = "\n".join(
            line for line in body.splitlines()
            if "::" not in line
        ).strip()
        if clean_body:
            lines.append(clean_body)
            lines.append("")

    # ── Recommended approach ──
    lines.append("## Recommended Approach")
    lines.append("")
    recs: list[str] = []

    if crm.get("next_action"):
        recs.append(f"Execute pending action: {crm['next_action']}")
    if not interactions:
        recs.append("No recorded interactions — schedule initial outreach.")
    elif interactions:
        last_int = interactions[-1]
        last_date = last_int.get("date", "unknown")
        recs.append(f"Last interaction was {last_date}. Review before contact.")
    if claims and not decisions:
        recs.append(f"{len(claims)} claims without decisions — evaluate for action.")
    if crm.get("pipeline_stage") == "identified":
        recs.append("Entity is identified but not engaged — move to engaged with specific next step.")

    for r in recs:
        lines.append(f"- {r}")
    lines.append("")

    return "\n".join(lines) + "\n"
