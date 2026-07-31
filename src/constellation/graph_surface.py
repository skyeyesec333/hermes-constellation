"""Graph projection and offline review surface.

Builds a cited, sensitivity-filtered projection of the vault's relationship
graph — canonical relationship records plus edges derived from claims,
decisions, observations, events, opportunities, and source citations — and
renders it as a fully self-contained offline HTML page (inline SVG, no
external resources, no JavaScript dependencies).

Candidate (review-required) packets are included but always visually and
structurally distinct from canonical facts. Invalid canonical records are
skipped and counted, never silently ignored.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .vault import is_initialized

_SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

# folder -> (edge_kind, predicate, entity reference fields, preferred time fields)
_RECORD_EDGE_SPECS: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    "decisions": ("decision", "decided_about", ("subject_id",), ("decided_at", "created_at")),
    "observations": ("observation", "observed_change_for", ("entity_ids",), ("created_at",)),
    "events": ("event", "event_involves", ("entity_ids",), ("event_date", "created_at")),
    "opportunities": ("opportunity", "opportunity_targets", ("subject_ids",), ("created_at",)),
}

_CITATION_FOLDERS = ("claims", "decisions")

_CANDIDATE_TYPES = {"claim", "decision", "observation", "event", "opportunity"}

_NODE_COLORS = {
    "company": "#1f6feb",
    "organization": "#1f6feb",
    "person": "#a371f7",
    "source_item": "#6e7681",
    "source-item": "#6e7681",
    "decision": "#d29922",
    "observation": "#8957e5",
    "event": "#3fb950",
    "opportunity": "#f778ba",
}


class GraphSurfaceError(RuntimeError):
    """Raised when the graph surface cannot be built safely."""


def _rank(value: Any) -> int:
    return _SENSITIVITY_RANK.get(str(value), 1)


def _scan_records(vault: Path, folder: str) -> tuple[list[dict[str, Any]], int]:
    """Return (parseable records, skipped invalid count) for a canonical folder."""
    base = vault / folder
    if not base.is_dir():
        return [], 0
    records: list[dict[str, Any]] = []
    skipped = 0
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1
            continue
        if metadata.get("id"):
            records.append(metadata)
        else:
            skipped += 1
    return records, skipped


def _record_time(record: dict[str, Any], preferred: tuple[str, ...]) -> str:
    for field in (*preferred, "updated_at", "created_at"):
        value = record.get(field)
        if value:
            return str(value)
    return ""


def _edge(
    *,
    edge_kind: str,
    edge_source: str,
    record: dict[str, Any],
    subject_id: str,
    object_id: str,
    predicate: str,
    folder: str,
    time_fields: tuple[str, ...] = ("created_at",),
    candidate: bool = False,
) -> dict[str, Any]:
    confidence = record.get("confidence")
    return {
        "edge_kind": edge_kind,
        "edge_source": edge_source,
        "record_id": str(record["id"]),
        "title": str(record.get("title", "")),
        "subject_id": subject_id,
        "object_id": object_id,
        "predicate": predicate,
        "evidence_class": str(record.get("evidence_class", "derived" if edge_kind == "claim" else "")),
        "confidence": confidence,
        "source_ids": [str(item) for item in (record.get("source_ids") or [])],
        "sensitivity": str(record.get("sensitivity", "internal")),
        "time": _record_time(record, time_fields),
        "updated_at": str(record.get("updated_at", "")),
        "record_path": f"{folder}/{record['id']}.md",
        "candidate": candidate,
    }


def _scan_candidate_packets(vault: Path) -> tuple[list[dict[str, Any]], int]:
    """Return (typed candidate packets, skipped invalid count)."""
    candidates_dir = vault / ".constellation/candidates"
    if not candidates_dir.is_dir():
        return [], 0
    packets: list[dict[str, Any]] = []
    skipped = 0
    for path in sorted(candidates_dir.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skipped += 1
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") == "relationship_candidate":
            record = payload.get("record")
            if isinstance(record, dict) and record.get("id"):
                packets.append(record)
            else:
                skipped += 1
            continue
        if payload.get("type") in _CANDIDATE_TYPES and payload.get("id"):
            packets.append(payload)
    return packets, skipped


def build_graph_projection(
    vault: Path | str,
    *,
    sensitivity_ceiling: str = "internal",
    entity_id: str | None = None,
    include_candidates: bool = True,
) -> dict[str, Any]:
    """Return a cited, sensitivity-filtered typed node/edge projection.

    Node types: companies/organizations, people, source-items, decisions,
    observations, events, opportunities. Edge kinds: relationship (canonical),
    claim (derived), decision/observation/event/opportunity (canonical record
    edges), citation (record -> source-item). Edges marked ``candidate`` come
    from review-required packets and are never canonical facts. When no edges
    survive filtering, the projection is explicitly degraded.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise GraphSurfaceError("vault is not initialized")
    ceiling = _SENSITIVITY_RANK.get(sensitivity_ceiling)
    if ceiling is None:
        raise GraphSurfaceError(f"unknown sensitivity ceiling: {sensitivity_ceiling}")

    skipped_invalid = 0
    node_records: dict[str, tuple[str, dict[str, Any], str]] = {}

    for folder in ("entities", "people", "source-items"):
        records, skipped = _scan_records(vault, folder)
        skipped_invalid += skipped
        for record in records:
            if _rank(record.get("sensitivity")) <= ceiling:
                node_records[str(record["id"])] = (
                    str(record.get("type", "entity")),
                    record,
                    folder,
                )

    edges: list[dict[str, Any]] = []

    def _add_typed_edges(record: dict[str, Any], *, candidate: bool) -> None:
        record_type = str(record.get("type", ""))
        folder_map = {
            "decision": "decisions",
            "observation": "observations",
            "event": "events",
            "opportunity": "opportunities",
        }
        folder = folder_map.get(record_type)
        if folder is None:
            return
        kind, predicate, ref_fields, time_fields = _RECORD_EDGE_SPECS[folder]
        targets: list[str] = []
        for field in ref_fields:
            value = record.get(field)
            if isinstance(value, list):
                targets.extend(str(item) for item in value)
            elif value:
                targets.append(str(value))
        targets = [t for t in targets if t in node_records]
        if not targets:
            return
        record_id = str(record["id"])
        if record_id not in node_records:
            node_records[record_id] = (record_type, record, folder)
        for target in targets:
            edges.append(_edge(
                edge_kind=kind,
                edge_source=f"candidate_{kind}" if candidate else f"canonical_{kind}",
                record=record,
                subject_id=record_id,
                object_id=target,
                predicate=predicate,
                folder=folder,
                time_fields=time_fields,
                candidate=candidate,
            ))

    # canonical relationships
    relationships, skipped = _scan_records(vault, "relationships")
    skipped_invalid += skipped
    for record in relationships:
        if _rank(record.get("sensitivity")) > ceiling:
            continue
        subject = str(record.get("subject_id", ""))
        obj = str(record.get("object_id", ""))
        if subject not in node_records or obj not in node_records:
            continue
        edges.append(_edge(
            edge_kind="relationship",
            edge_source="canonical_relationship",
            record=record,
            subject_id=subject,
            object_id=obj,
            predicate=str(record.get("predicate", "")),
            folder="relationships",
        ))

    # claims: entity-to-entity derived edges + citation edges to source-items
    claims, skipped = _scan_records(vault, "claims")
    skipped_invalid += skipped
    source_ids_known = {
        record_id for record_id, (kind, _, folder) in node_records.items() if folder == "source-items"
    }
    for record in claims:
        if _rank(record.get("sensitivity")) > ceiling:
            continue
        subject = str(record.get("subject_id", ""))
        obj = str(record.get("object_id", "") or "")
        if obj and subject in node_records and obj in node_records:
            edges.append(_edge(
                edge_kind="claim",
                edge_source="derived_from_claim",
                record=record,
                subject_id=subject,
                object_id=obj,
                predicate=str(record.get("predicate", "")),
                folder="claims",
            ))
        for source_id in (record.get("source_ids") or []):
            source_key = str(source_id)
            if subject in node_records and source_key in source_ids_known:
                edges.append(_edge(
                    edge_kind="citation",
                    edge_source="citation",
                    record=record,
                    subject_id=subject,
                    object_id=source_key,
                    predicate="evidenced_by",
                    folder="claims",
                ))

    # canonical typed record edges (decisions, observations, events, opportunities)
    for folder in _RECORD_EDGE_SPECS:
        records, skipped = _scan_records(vault, folder)
        skipped_invalid += skipped
        for record in records:
            if _rank(record.get("sensitivity")) > ceiling:
                continue
            _add_typed_edges(record, candidate=False)

    # decision citation edges to source-items
    decisions, _ = _scan_records(vault, "decisions")
    for record in decisions:
        if _rank(record.get("sensitivity")) > ceiling:
            continue
        subject = str(record.get("subject_id", ""))
        for source_id in (record.get("source_ids") or []):
            source_key = str(source_id)
            if subject in node_records and source_key in source_ids_known:
                edges.append(_edge(
                    edge_kind="citation",
                    edge_source="citation",
                    record=record,
                    subject_id=subject,
                    object_id=source_key,
                    predicate="evidenced_by",
                    folder="decisions",
                ))

    # candidate packets: same shapes, always flagged
    candidate_count = 0
    if include_candidates:
        packets, skipped = _scan_candidate_packets(vault)
        skipped_invalid += skipped
        for packet in packets:
            if _rank(packet.get("sensitivity")) > ceiling:
                continue
            if str(packet.get("type")) == "claim":
                subject = str(packet.get("subject_id", ""))
                obj = str(packet.get("object_id", "") or "")
                if obj and subject in node_records and obj in node_records:
                    edges.append(_edge(
                        edge_kind="claim",
                        edge_source="candidate_claim",
                        record=packet,
                        subject_id=subject,
                        object_id=obj,
                        predicate=str(packet.get("predicate", "")),
                        folder=".constellation/candidates",
                        candidate=True,
                    ))
                    candidate_count += 1
            elif str(packet.get("type")) == "relationship":
                subject = str(packet.get("subject_id", ""))
                obj = str(packet.get("object_id", ""))
                if subject in node_records and obj in node_records:
                    edges.append(_edge(
                        edge_kind="relationship",
                        edge_source="candidate_relationship",
                        record=packet,
                        subject_id=subject,
                        object_id=obj,
                        predicate=str(packet.get("predicate", "")),
                        folder=".constellation/candidates",
                        candidate=True,
                    ))
                    candidate_count += 1
            else:
                before = len(edges)
                _add_typed_edges(packet, candidate=True)
                candidate_count += len(edges) - before

    if entity_id is not None:
        edges = [e for e in edges if entity_id in {e["subject_id"], e["object_id"]}]

    edges.sort(key=lambda e: (str(e["predicate"]), str(e["record_id"]), str(e["object_id"])))

    used_ids = {str(e["subject_id"]) for e in edges} | {str(e["object_id"]) for e in edges}
    nodes = [
        {
            "id": node_id,
            "title": str(node_records[node_id][1].get("title", node_id)),
            "type": node_records[node_id][0],
            "sensitivity": str(node_records[node_id][1].get("sensitivity", "internal")),
            "updated_at": str(node_records[node_id][1].get("updated_at", "")),
            "record_path": f"{node_records[node_id][2]}/{node_id}.md",
        }
        for node_id in sorted(used_ids)
    ]

    degraded = not edges
    return {
        "nodes": nodes,
        "edges": edges,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "candidate_edges": sum(1 for e in edges if e["candidate"]),
        "skipped_invalid_records": skipped_invalid,
        "degraded": degraded,
        "degraded_reason": "no relationships or claim-derived edges within ceiling" if degraded else None,
        "sensitivity_ceiling": sensitivity_ceiling,
        "focus_entity_id": entity_id,
    }


def _node_color(node_type: str) -> str:
    return _NODE_COLORS.get(node_type, "#1f6feb")


_LAYOUT_WIDTH = _LAYOUT_HEIGHT = 900
_LAYOUT_RADIUS = 340


def layout_projection(projection: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Deterministic circular layout for a projection.

    Shared geometry between the offline HTML render and API consumers (the
    Hermes dashboard plugin), so every surface draws the same graph. Nodes
    are placed in projection order (already id-sorted), so identical
    projections always produce identical coordinates.
    """
    nodes: list[dict[str, Any]] = projection["nodes"]
    cx, cy = _LAYOUT_WIDTH / 2, _LAYOUT_HEIGHT / 2
    positions: dict[str, tuple[float, float]] = {}
    for index, node in enumerate(nodes):
        angle = 2 * math.pi * index / max(len(nodes), 1) - math.pi / 2
        positions[str(node["id"])] = (
            cx + _LAYOUT_RADIUS * math.cos(angle),
            cy + _LAYOUT_RADIUS * math.sin(angle),
        )
    return positions


def render_graph_surface(projection: dict[str, Any]) -> str:
    """Render the projection as a self-contained offline HTML page.

    Inline SVG with a deterministic radial layout; no external resources,
    no scripts — the artifact is a read-only review surface. Candidate edges
    are always visually distinct (orange, dotted) from canonical facts.
    """
    nodes: list[dict[str, Any]] = projection["nodes"]
    edges: list[dict[str, Any]] = projection["edges"]

    width = height = _LAYOUT_WIDTH
    positions = layout_projection(projection)

    parts: list[str] = []
    parts.append(f'<svg viewBox="0 0 {width} {height}" '
                 'style="background:#0d1117;max-width:100%">')
    for edge in edges:
        x1, y1 = positions[str(edge["subject_id"])]
        x2, y2 = positions[str(edge["object_id"])]
        if edge["candidate"]:
            color, dash, width_px = "#f0883e", ' stroke-dasharray="2,3"', "1.5"
        elif edge["edge_kind"] == "relationship":
            color, dash, width_px = "#58a6ff", "", "1.5"
        elif edge["edge_kind"] == "citation":
            color, dash, width_px = "#3fb950", ' stroke-dasharray="1,3"', "1"
        else:
            color, dash, width_px = "#8b949e", ' stroke-dasharray="5,4"', "1.5"
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="{width_px}"{dash}>'
            f"<title>{html.escape(str(edge['predicate']))} "
            f"({html.escape(str(edge['edge_source']))}; sources: "
            f"{html.escape(', '.join(edge['source_ids']) or 'none')}; "
            f"updated: {html.escape(str(edge['updated_at']))})</title></line>"
        )
    for node in nodes:
        x, y = positions[str(node["id"])]
        title = html.escape(str(node["title"]))
        color = _node_color(str(node["type"]))
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="16" fill="{color}">'
            f"<title>{html.escape(str(node['type']))} — "
            f"{html.escape(str(node['record_path']))}</title></circle>"
            f'<text x="{x:.1f}" y="{y + 34:.1f}" fill="#c9d1d9" font-size="13" '
            f'font-family="system-ui,sans-serif" text-anchor="middle">{title}</text>'
        )
    parts.append("</svg>")

    def _confidence_text(value: Any) -> str:
        return "" if value is None else str(value)

    edge_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(e['predicate']))}</td>"
        f"<td>{html.escape(str(e['edge_kind']))}</td>"
        f"<td>{html.escape(str(e['edge_source']))}</td>"
        f"<td>{html.escape(str(e['evidence_class']))}</td>"
        f"<td>{html.escape(_confidence_text(e['confidence']))}</td>"
        f"<td>{html.escape(', '.join(e['source_ids']) or 'none')}</td>"
        f"<td>{html.escape(str(e['sensitivity']))}</td>"
        f"<td>{'candidate' if e['candidate'] else ''}</td>"
        "</tr>"
        for e in edges
    )
    legend = (
        '<p style="color:#8b949e">Solid blue = canonical relationship; '
        "dashed grey = derived/typed record edge; dotted green = source citation; "
        "dotted orange = candidate (review-required, not canonical fact). "
        "Hover edges for source citations.</p>"
    )
    if projection["degraded"]:
        legend += (
            f'<p style="color:#f0883e">Degraded: '
            f"{html.escape(str(projection['degraded_reason']))}.</p>"
        )
    if projection.get("skipped_invalid_records"):
        legend += (
            f'<p style="color:#f0883e">Note: '
            f"{projection['skipped_invalid_records']} invalid record(s) skipped.</p>"
        )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Constellation Graph Surface</title></head>"
        '<body style="background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif">'
        "<h1>Relationship Graph</h1>"
        f"<p>{projection['total_nodes']} nodes, {projection['total_edges']} edges "
        f"({projection.get('candidate_edges', 0)} candidate; "
        f"ceiling: {html.escape(str(projection['sensitivity_ceiling']))})</p>"
        + "".join(parts)
        + legend
        + '<table border="1" cellpadding="4" style="border-collapse:collapse;color:#c9d1d9">'
        "<tr><th>predicate</th><th>kind</th><th>edge source</th><th>evidence class</th>"
        "<th>confidence</th><th>source ids</th><th>sensitivity</th><th>candidate</th></tr>"
        f"{edge_rows}</table></body></html>"
    )
