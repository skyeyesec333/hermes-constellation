"""Canonical-derived wiki views — rebuildable dossiers, never truth.

Views are generated projections of canonical records: every claim cites its
record path and sources, contradictions stay visible, and the rendered file
declares itself generated. Views live under ``views/`` — never in canonical
folders — and rebuilding the same vault state is byte-deterministic.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .vault import is_initialized

_SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


class WikiViewError(RuntimeError):
    """Raised when a view cannot be built safely."""


def _scan(vault: Path, folder: str) -> list[tuple[str, dict[str, Any]]]:
    base = vault / folder
    if not base.is_dir():
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if metadata.get("id"):
            out.append((f"{folder}/{path.name}", metadata))
    return out


def build_entity_dossier(
    vault: Path | str, entity_id: str, *, sensitivity_ceiling: str = "internal"
) -> dict[str, Any]:
    """Project a cited dossier for one entity from canonical records."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise WikiViewError("vault is not initialized")
    ceiling = _SENSITIVITY_RANK.get(sensitivity_ceiling)
    if ceiling is None:
        raise WikiViewError(f"unknown sensitivity ceiling: {sensitivity_ceiling}")

    entity: dict[str, Any] | None = None
    for folder in ("entities", "people"):
        for _, metadata in _scan(vault, folder):
            if str(metadata["id"]) == entity_id:
                entity = metadata
                break
    if entity is None:
        raise WikiViewError(f"entity not found: {entity_id}")

    claims: list[dict[str, Any]] = []
    for record_path, metadata in _scan(vault, "claims"):
        if str(metadata.get("subject_id", "")) != entity_id:
            continue
        if _SENSITIVITY_RANK.get(str(metadata.get("sensitivity")), 1) > ceiling:
            continue
        claims.append({
            "id": str(metadata["id"]),
            "predicate": str(metadata.get("predicate", "")),
            "object": str(metadata.get("object_literal") or metadata.get("object_id") or ""),
            "status": str(metadata.get("status", "")),
            "source_ids": [str(s) for s in metadata.get("source_ids", [])],
            "record_path": record_path,
        })
    claims.sort(key=lambda c: (c["predicate"], c["id"]))

    by_key: dict[str, set[str]] = defaultdict(set)
    for claim in claims:
        if claim["status"] == "active" and claim["object"]:
            by_key[claim["predicate"]].add(claim["object"])
    contradictions = sorted(
        f"predicate '{predicate}' has conflicting active values: {sorted(values)}"
        for predicate, values in by_key.items()
        if len(values) > 1
    )

    decisions = [
        {"id": str(m["id"]), "title": str(m.get("title", "")), "record_path": rp}
        for rp, m in _scan(vault, "decisions")
        if str(m.get("subject_id", "")) == entity_id
    ]
    opportunities = [
        {"id": str(m["id"]), "title": str(m.get("title", "")),
         "stage": str(m.get("stage", "")), "record_path": rp}
        for rp, m in _scan(vault, "opportunities")
        if entity_id in [str(s) for s in m.get("subject_ids", [])]
    ]

    return {
        "entity": {
            "id": entity_id,
            "title": str(entity.get("title", entity_id)),
            "type": str(entity.get("type", "")),
            "sensitivity": str(entity.get("sensitivity", "internal")),
        },
        "claims": claims,
        "contradictions": contradictions,
        "decisions": sorted(decisions, key=lambda d: d["id"]),
        "opportunities": sorted(opportunities, key=lambda o: o["id"]),
        "sensitivity_ceiling": sensitivity_ceiling,
    }


def render_dossier_markdown(dossier: dict[str, Any]) -> str:
    """Render the dossier projection as deterministic Markdown."""
    entity = dossier["entity"]
    lines = [
        f"# Dossier — {entity['title']}",
        "",
        "> **GENERATED VIEW — not a canonical record.** Rebuild with "
        f"`constellation dossier <vault> {entity['id']}`. Edits must be made "
        "through reviewed candidates against canonical records, never here.",
        "",
        f"- type: {entity['type']}",
        f"- sensitivity: {entity['sensitivity']}",
        f"- ceiling: {dossier['sensitivity_ceiling']}",
        "",
        "## Claims",
        "",
    ]
    if not dossier["claims"]:
        lines.append("No claims reference this entity within the ceiling.")
    for claim in dossier["claims"]:
        sources = ", ".join(claim["source_ids"]) or "none"
        lines.append(
            f"- **{claim['predicate']}** → {claim['object']} "
            f"(status: {claim['status']}; sources: {sources}; "
            f"record: `{claim['record_path']}`)"
        )
    lines += ["", "## Contradictions", ""]
    if dossier["contradictions"]:
        lines += [f"- {item}" for item in dossier["contradictions"]]
    else:
        lines.append("None detected.")
    lines += ["", "## Decisions", ""]
    lines += (
        [f"- {d['title']} (`{d['record_path']}`)" for d in dossier["decisions"]]
        or ["None recorded."]
    )
    lines += ["", "## Opportunities", ""]
    lines += (
        [f"- {o['title']} (stage: {o['stage']}; `{o['record_path']}`)" for o in dossier["opportunities"]]
        or ["None recorded."]
    )
    lines.append("")
    return "\n".join(lines)
