"""Graph projection and offline review surface.

Builds a cited, sensitivity-filtered projection of the vault's relationship
graph — canonical relationship records plus edges derived from claims — and
renders it as a fully self-contained offline HTML page (inline SVG, no
external resources, no JavaScript dependencies).
"""

from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .vault import is_initialized

_SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


class GraphSurfaceError(RuntimeError):
    """Raised when the graph surface cannot be built safely."""


def _rank(value: Any) -> int:
    return _SENSITIVITY_RANK.get(str(value), 1)


def _scan_records(vault: Path, folder: str) -> list[dict[str, Any]]:
    base = vault / folder
    if not base.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if metadata.get("id"):
            records.append(metadata)
    return records


def build_graph_projection(
    vault: Path | str,
    *,
    sensitivity_ceiling: str = "internal",
    entity_id: str | None = None,
) -> dict[str, Any]:
    """Return a cited, sensitivity-filtered node/edge projection.

    Edges are marked ``canonical_relationship`` or ``derived_from_claim`` so
    derived evidence is always distinguishable from canonical relationships.
    When no edges survive filtering, the projection is explicitly degraded.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise GraphSurfaceError("vault is not initialized")
    ceiling = _SENSITIVITY_RANK.get(sensitivity_ceiling)
    if ceiling is None:
        raise GraphSurfaceError(f"unknown sensitivity ceiling: {sensitivity_ceiling}")

    entities = {
        str(record["id"]): record
        for record in _scan_records(vault, "entities")
        if _rank(record.get("sensitivity")) <= ceiling
    }
    people = {
        str(record["id"]): record
        for record in _scan_records(vault, "people")
        if _rank(record.get("sensitivity")) <= ceiling
    }
    nodes_by_id = {**entities, **people}

    edges: list[dict[str, Any]] = []
    for record in _scan_records(vault, "relationships"):
        if _rank(record.get("sensitivity")) > ceiling:
            continue
        subject = str(record.get("subject_id", ""))
        obj = str(record.get("object_id", ""))
        if subject not in nodes_by_id or obj not in nodes_by_id:
            continue
        edges.append({
            "edge_source": "canonical_relationship",
            "record_id": str(record["id"]),
            "subject_id": subject,
            "object_id": obj,
            "predicate": str(record.get("predicate", "")),
            "evidence_class": str(record.get("evidence_class", "")),
            "source_ids": [str(item) for item in record.get("source_ids", [])],
            "sensitivity": str(record.get("sensitivity", "internal")),
        })
    for record in _scan_records(vault, "claims"):
        if _rank(record.get("sensitivity")) > ceiling:
            continue
        subject = str(record.get("subject_id", ""))
        obj = str(record.get("object_id", ""))
        if not obj or subject not in nodes_by_id or obj not in nodes_by_id:
            continue
        edges.append({
            "edge_source": "derived_from_claim",
            "record_id": str(record["id"]),
            "subject_id": subject,
            "object_id": obj,
            "predicate": str(record.get("predicate", "")),
            "evidence_class": "derived",
            "source_ids": [str(item) for item in record.get("source_ids", [])],
            "sensitivity": str(record.get("sensitivity", "internal")),
        })

    if entity_id is not None:
        edges = [e for e in edges if entity_id in {e["subject_id"], e["object_id"]}]

    edges.sort(key=lambda e: (str(e["predicate"]), str(e["record_id"])))

    used_ids = {str(e["subject_id"]) for e in edges} | {str(e["object_id"]) for e in edges}
    nodes = [
        {
            "id": node_id,
            "title": str(nodes_by_id[node_id].get("title", node_id)),
            "type": str(nodes_by_id[node_id].get("type", "entity")),
            "sensitivity": str(nodes_by_id[node_id].get("sensitivity", "internal")),
        }
        for node_id in sorted(used_ids)
    ]

    degraded = not edges
    return {
        "nodes": nodes,
        "edges": edges,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "degraded": degraded,
        "degraded_reason": "no relationships or claim-derived edges within ceiling" if degraded else None,
        "sensitivity_ceiling": sensitivity_ceiling,
        "focus_entity_id": entity_id,
    }


def render_graph_surface(projection: dict[str, Any]) -> str:
    """Render the projection as a self-contained offline HTML page.

    Inline SVG with a deterministic radial layout; no external resources,
    no scripts — the artifact is a read-only review surface.
    """
    nodes: list[dict[str, Any]] = projection["nodes"]
    edges: list[dict[str, Any]] = projection["edges"]

    width = height = 900
    cx, cy, radius = width / 2, height / 2, 340
    positions: dict[str, tuple[float, float]] = {}
    for index, node in enumerate(nodes):
        angle = 2 * math.pi * index / max(len(nodes), 1) - math.pi / 2
        positions[str(node["id"])] = (
            cx + radius * math.cos(angle),
            cy + radius * math.sin(angle),
        )

    parts: list[str] = []
    parts.append(f'<svg viewBox="0 0 {width} {height}" '
                 'style="background:#0d1117;max-width:100%">')
    for edge in edges:
        x1, y1 = positions[str(edge["subject_id"])]
        x2, y2 = positions[str(edge["object_id"])]
        color = "#58a6ff" if edge["edge_source"] == "canonical_relationship" else "#8b949e"
        dash = "" if edge["edge_source"] == "canonical_relationship" else ' stroke-dasharray="5,4"'
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="1.5"{dash}>'
            f"<title>{html.escape(str(edge['predicate']))} "
            f"({html.escape(str(edge['edge_source']))}; sources: "
            f"{html.escape(', '.join(edge['source_ids']) or 'none')})</title></line>"
        )
    for node in nodes:
        x, y = positions[str(node["id"])]
        title = html.escape(str(node["title"]))
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="16" fill="#1f6feb"/>'
            f'<text x="{x:.1f}" y="{y + 34:.1f}" fill="#c9d1d9" font-size="13" '
            f'font-family="system-ui,sans-serif" text-anchor="middle">{title}</text>'
        )
    parts.append("</svg>")

    edge_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(e['predicate']))}</td>"
        f"<td>{html.escape(str(e['edge_source']))}</td>"
        f"<td>{html.escape(str(e['evidence_class']))}</td>"
        f"<td>{html.escape(', '.join(e['source_ids']) or 'none')}</td>"
        f"<td>{html.escape(str(e['sensitivity']))}</td>"
        "</tr>"
        for e in edges
    )
    legend = (
        '<p style="color:#8b949e">Solid blue = canonical relationship; '
        "dashed grey = derived from claim. Hover edges for source citations.</p>"
    )
    if projection["degraded"]:
        legend += (
            f'<p style="color:#f0883e">Degraded: '
            f"{html.escape(str(projection['degraded_reason']))}.</p>"
        )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Constellation Graph Surface</title></head>"
        '<body style="background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif">'
        "<h1>Relationship Graph</h1>"
        f"<p>{projection['total_nodes']} nodes, {projection['total_edges']} edges "
        f"(ceiling: {html.escape(str(projection['sensitivity_ceiling']))})</p>"
        + "".join(parts)
        + legend
        + '<table border="1" cellpadding="4" style="border-collapse:collapse;color:#c9d1d9">'
        "<tr><th>predicate</th><th>edge source</th><th>evidence class</th>"
        "<th>source ids</th><th>sensitivity</th></tr>"
        f"{edge_rows}</table></body></html>"
    )
