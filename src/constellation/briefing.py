"""Cited evidence briefings rendered from the typed graph projection.

Composes the Wave 4 projection APIs into a compliance-readable briefing for
one canonical subject: canonical relationships, claims with confidence and
their citation chains to real source-items, typed records (decisions,
observations, events, opportunities), and review-required candidates —
always flagged, never blended into canonical facts.

Renders to markdown or fully offline HTML (inline CSS, no scripts, no
external resources) suitable for air-gapped review.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .graph_surface import build_graph_projection

_CEILINGS = ("public", "internal", "confidential", "restricted")
_TYPED_KINDS = ("decision", "observation", "event", "opportunity")


class BriefingError(RuntimeError):
    """Raised when a briefing cannot be built."""


def build_entity_briefing(
    root: Path | str,
    entity_id: str,
    *,
    sensitivity_ceiling: str = "internal",
) -> dict[str, Any]:
    """Assemble the cited briefing model for one canonical subject."""
    if sensitivity_ceiling not in _CEILINGS:
        raise BriefingError(f"unknown sensitivity ceiling: {sensitivity_ceiling}")
    vault = Path(root).absolute()
    projection = build_graph_projection(
        vault, sensitivity_ceiling=sensitivity_ceiling, include_candidates=True
    )
    nodes = {n["id"]: n for n in projection["nodes"]}
    entity = nodes.get(entity_id)
    if entity is None:
        raise BriefingError(f"entity not found within ceiling: {entity_id}")

    touching = [
        e for e in projection["edges"]
        if e["subject_id"] == entity_id or e["object_id"] == entity_id
    ]

    citation_targets: dict[str, list[str]] = {}
    for edge in projection["edges"]:
        if edge["edge_kind"] != "citation" or edge["candidate"]:
            continue
        target = nodes.get(edge["object_id"])
        if target is None or target["type"] not in ("source_item", "source-item"):
            continue
        # citation edges carry the citing record (claim/decision) as record_id
        citation_targets.setdefault(edge["record_id"], []).append(target["record_path"])

    relationships: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    seen_claims: set[str] = set()
    typed: dict[str, list[dict[str, Any]]] = {k: [] for k in _TYPED_KINDS}
    candidates: list[dict[str, Any]] = []

    for edge in sorted(touching, key=lambda e: (e["edge_kind"], e["predicate"], e["record_path"])):
        record_id = edge["record_id"]
        record = nodes.get(record_id)
        title = edge.get("title") or (record["title"] if record else record_id)
        folder = edge["record_path"].split("/", 1)[0]
        if edge["candidate"]:
            candidates.append({
                "candidate": True,
                "kind": folder.rstrip("s"),
                "title": title,
                "record_path": edge["record_path"],
            })
            continue
        if edge["edge_kind"] == "relationship":
            outgoing = edge["subject_id"] == entity_id
            other_id = edge["object_id"] if outgoing else edge["subject_id"]
            other = nodes.get(other_id)
            relationships.append({
                "predicate": edge["predicate"],
                "other_title": other["title"] if other else other_id,
                "outgoing": outgoing,
                "evidence_class": edge.get("evidence_class"),
                "confidence": edge.get("confidence"),
                "record_path": edge["record_path"],
            })
        elif edge["edge_kind"] == "claim" or (
            edge["edge_kind"] == "citation" and folder == "claims"
        ):
            # claims without an object_id surface only as citation edges;
            # dedupe so multi-source claims appear once with all citations
            if record_id in seen_claims:
                continue
            seen_claims.add(record_id)
            claims.append({
                "title": title,
                "confidence": edge.get("confidence"),
                "record_path": edge["record_path"],
                "citations": citation_targets.get(record_id, []),
            })
        elif edge["edge_kind"] in _TYPED_KINDS:
            typed[edge["edge_kind"]].append({
                "title": title,
                "record_path": edge["record_path"],
                "updated_at": edge.get("updated_at", ""),
            })

    return {
        "entity": {
            "id": entity_id,
            "title": entity["title"],
            "type": entity["type"],
            "sensitivity": entity["sensitivity"],
            "record_path": entity["record_path"],
        },
        "sensitivity_ceiling": sensitivity_ceiling,
        "generated_from": "typed graph projection (deterministic)",
        "relationships": relationships,
        "claims": claims,
        "decisions": typed["decision"],
        "observations": typed["observation"],
        "events": typed["event"],
        "opportunities": typed["opportunity"],
        "candidates": candidates,
    }


def render_briefing_markdown(briefing: dict[str, Any]) -> str:
    """Render the briefing model as cited markdown."""
    entity = briefing["entity"]
    lines = [
        f"# Briefing: {entity['title']}",
        "",
        f"**Type:** {entity['type']}  ",
        f"**Record:** {entity['record_path']}  ",
        f"**Ceiling:** {briefing['sensitivity_ceiling']}",
        "",
    ]
    if briefing["relationships"]:
        lines += ["## Canonical Relationships", ""]
        for rel in briefing["relationships"]:
            arrow = "→" if rel["outgoing"] else "←"
            conf = f" (confidence {rel['confidence']})" if rel["confidence"] is not None else ""
            lines.append(
                f"- {arrow} **{rel['predicate']}** {rel['other_title']}{conf} — `{rel['record_path']}`"
            )
        lines.append("")
    if briefing["claims"]:
        lines += ["## Claims (with citations)", ""]
        for claim in briefing["claims"]:
            conf = f" — confidence {claim['confidence']}" if claim["confidence"] is not None else ""
            lines.append(f"- {claim['title']}{conf} — `{claim['record_path']}`")
            for citation in claim["citations"]:
                lines.append(f"  - cited: `{citation}`")
        lines.append("")
    for kind in _TYPED_KINDS:
        records = briefing["opportunities" if kind == "opportunity" else kind + "s"]
        if records:
            lines += [f"## {kind.title()}s", ""]
            for rec in records:
                lines.append(f"- {rec['title']} — `{rec['record_path']}`")
            lines.append("")
    if briefing["candidates"]:
        lines += ["## Review-Required Candidates", ""]
        lines.append(
            "CANDIDATE records below are unverified and excluded from canonical facts "
            "until promoted via `constellation review`."
        )
        lines.append("")
        for cand in briefing["candidates"]:
            lines.append(f"- [CANDIDATE] {cand['title']} ({cand['kind']}) — `{cand['record_path']}`")
        lines.append("")
    lines.append(f"_Generated from {briefing['generated_from']}._")
    return "\n".join(lines) + "\n"


_CSS = """
body { background: #0d1117; color: #c9d1d9; font-family: system-ui, sans-serif;
       max-width: 860px; margin: 24px auto; padding: 0 16px; }
h1 { color: #f0f6fc; }
h2 { color: #58a6ff; border-bottom: 1px solid #21262d; padding-bottom: 4px; }
code { background: #161b22; padding: 1px 4px; border-radius: 4px; font-size: 12px; }
.meta { color: #8b949e; font-size: 13px; }
.candidate { color: #f0883e; }
ul { line-height: 1.6; }
.cites li { color: #3fb950; font-size: 13px; }
"""


def render_briefing_html(briefing: dict[str, Any]) -> str:
    """Render the briefing model as fully offline, self-contained HTML."""
    entity = briefing["entity"]
    parts = [
        "<!DOCTYPE html>",
        "<html><head><meta charset=\"utf-8\">",
        f"<title>Briefing: {escape(entity['title'])}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>Briefing: {escape(entity['title'])}</h1>",
        f"<p class=\"meta\">Type: {escape(entity['type'])} · Record: "
        f"<code>{escape(entity['record_path'])}</code> · Ceiling: "
        f"{escape(briefing['sensitivity_ceiling'])}</p>",
    ]
    if briefing["relationships"]:
        parts.append("<h2>Canonical Relationships</h2><ul>")
        for rel in briefing["relationships"]:
            arrow = "→" if rel["outgoing"] else "←"
            conf = f" (confidence {rel['confidence']})" if rel["confidence"] is not None else ""
            parts.append(
                f"<li>{arrow} <b>{escape(rel['predicate'])}</b> "
                f"{escape(rel['other_title'])}{escape(conf)} — "
                f"<code>{escape(rel['record_path'])}</code></li>"
            )
        parts.append("</ul>")
    if briefing["claims"]:
        parts.append("<h2>Claims (with citations)</h2><ul>")
        for claim in briefing["claims"]:
            conf = f" — confidence {claim['confidence']}" if claim["confidence"] is not None else ""
            parts.append(
                f"<li>{escape(claim['title'])}{escape(conf)} — "
                f"<code>{escape(claim['record_path'])}</code>"
            )
            if claim["citations"]:
                parts.append("<ul class=\"cites\">")
                for citation in claim["citations"]:
                    parts.append(f"<li>cited: <code>{escape(citation)}</code></li>")
                parts.append("</ul>")
            parts.append("</li>")
        parts.append("</ul>")
    for kind in _TYPED_KINDS:
        records = briefing["opportunities" if kind == "opportunity" else kind + "s"]
        if records:
            parts.append(f"<h2>{kind.title()}s</h2><ul>")
            for rec in records:
                parts.append(
                    f"<li>{escape(rec['title'])} — <code>{escape(rec['record_path'])}</code></li>"
                )
            parts.append("</ul>")
    if briefing["candidates"]:
        parts.append("<h2 class=\"candidate\">Review-Required Candidates</h2>")
        parts.append(
            "<p class=\"candidate\">CANDIDATE records are unverified and excluded "
            "from canonical facts until promoted via the review CLI.</p><ul>"
        )
        for cand in briefing["candidates"]:
            parts.append(
                f"<li class=\"candidate\">[CANDIDATE] {escape(cand['title'])} "
                f"({escape(cand['kind'])}) — <code>{escape(cand['record_path'])}</code></li>"
            )
        parts.append("</ul>")
    parts.append(f"<p class=\"meta\">Generated from {escape(briefing['generated_from'])}.</p>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"
