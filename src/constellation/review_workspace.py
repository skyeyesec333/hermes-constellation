"""Source-to-entity review workspace.

Builds a read-only projection for one source-item: preserved-source metadata,
extracted text split into stable anchors, and every review candidate that
references the source — each with explicit approve/reject commands. Renders
as a fully offline HTML page. No egress; external research stays
profile-gated and is only ever shown as a command suggestion.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .storage import safe_relative_path
from .vault import is_initialized


class ReviewWorkspaceError(RuntimeError):
    """Raised when the workspace cannot be built safely."""


def _load_source(vault: Path, source_id: str) -> tuple[dict[str, Any], str]:
    path = safe_relative_path(vault, Path("source-items") / f"{source_id}.md")
    if not path.is_file() or path.is_symlink():
        raise ReviewWorkspaceError(f"source-item not found: {source_id}")
    metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return metadata, f"source-items/{source_id}.md"


def _anchors(vault: Path, text_path: str | None) -> list[dict[str, Any]]:
    if not text_path:
        return []
    path = safe_relative_path(vault, text_path)
    if not path.is_file() or path.is_symlink():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    anchors: list[dict[str, Any]] = []
    for index, block in enumerate(b for b in text.split("\n\n") if b.strip()):
        anchors.append({
            "anchor_id": f"a{index + 1}",
            "text": block.strip()[:2000],
        })
    return anchors


def _related_candidates(vault: Path, source_id: str) -> list[dict[str, Any]]:
    candidates_dir = vault / ".constellation/candidates"
    related: list[dict[str, Any]] = []
    if not candidates_dir.is_dir():
        return related
    for path in sorted(candidates_dir.glob("*.json")):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        blob = json.dumps(candidate)
        if source_id not in blob:
            continue
        cid = str(candidate.get("id", path.stem))
        related.append({
            "id": cid,
            "title": str(candidate.get("title", cid)),
            "type": str(candidate.get("type", "candidate-patch")),
            "status": str(candidate.get("status", "unknown")),
            "candidate_path": f".constellation/candidates/{path.name}",
            "approve_command": f"constellation review <vault> promote --candidate {cid} --confirm",
            "reject_command": f"constellation review <vault> reject --candidate {cid} --confirm",
        })
    return related


def build_review_workspace(vault: Path | str, source_id: str) -> dict[str, Any]:
    """Project one source-item with its anchors and staged candidates."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise ReviewWorkspaceError("vault is not initialized")
    metadata, record_path = _load_source(vault, source_id)
    anchors = _anchors(vault, metadata.get("extracted_text_path"))
    related = _related_candidates(vault, source_id)
    return {
        "source": {
            "id": source_id,
            "title": str(metadata.get("title", source_id)),
            "media_type": str(metadata.get("media_type", "unknown")),
            "sensitivity": str(metadata.get("sensitivity", "internal")),
            "record_path": record_path,
            "original_path": str(metadata.get("original_path", "")),
            "extraction_status": str(metadata.get("extraction_status", "")),
        },
        "anchors": anchors,
        "related_candidates": related,
        "empty": not related,
        "research_command": (
            f"constellation inquiry <vault> run --question \"<question>\" "
            f"--subject-ids {source_id} --profile low"
        ),
    }


def render_review_workspace(workspace: dict[str, Any]) -> str:
    """Render the workspace projection as a self-contained offline HTML page."""
    source = workspace["source"]
    title = html.escape(str(source["title"]))

    anchor_blocks = "".join(
        f'<div style="border:1px solid #30363d;border-radius:6px;padding:8px;margin:6px 0">'
        f'<span style="color:#8b949e;font-size:11px">[{html.escape(str(a["anchor_id"]))}]</span> '
        f'<span style="color:#c9d1d9">{html.escape(str(a["text"]))}</span></div>'
        for a in workspace["anchors"]
    ) or '<p style="color:#8b949e">No extracted text anchors available.</p>'

    candidate_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(c['title']))}</td>"
        f"<td>{html.escape(str(c['status']))}</td>"
        f"<td><code>{html.escape(str(c['approve_command']))}</code></td>"
        f"<td><code>{html.escape(str(c['reject_command']))}</code></td>"
        "</tr>"
        for c in workspace["related_candidates"]
    )
    candidates_table = (
        '<table border="1" cellpadding="4" style="border-collapse:collapse;color:#c9d1d9">'
        "<tr><th>candidate</th><th>status</th><th>approve</th><th>reject</th></tr>"
        f"{candidate_rows}</table>"
        if candidate_rows
        else '<p style="color:#8b949e">No staged candidates reference this source yet.</p>'
    )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Source Review — {title}</title></head>"
        '<body style="background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif">'
        f"<h1>Source Review — {title}</h1>"
        f"<p>media: {html.escape(str(source['media_type']))} · "
        f"sensitivity: {html.escape(str(source['sensitivity']))} · "
        f"record: <code>{html.escape(str(source['record_path']))}</code> · "
        f"original: <code>{html.escape(str(source['original_path']))}</code></p>"
        "<h2>Extracted text anchors</h2>"
        + anchor_blocks
        + "<h2>Staged candidates</h2>"
        + candidates_table
        + "<h2>External research (profile-gated)</h2>"
        f'<p>Discovery stays off unless explicitly run. Suggested bounded command:</p>'
        f"<p><code>{html.escape(str(workspace['research_command']))}</code></p>"
        '<p style="color:#8b949e">Profiles: off (read-only) · low (default) · '
        "standard · deep (explicit escalation). Receipts record ceilings, "
        "lanes, and stop reasons.</p>"
        "</body></html>"
    )
