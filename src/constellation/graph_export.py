"""Bounded graph export with evidence manifest (i2-successor Wave 6 Task 6.1).

Exports the canonical graph (nodes + edges with citations, confidence, and
sensitivity) as deterministic JSON or NDJSON plus a receipt sidecar carrying
the manifest hash and excluded counts. Privacy contract: never include body
text, note paths, suppressed titles, or hidden-source names — nodes and
edges carry IDs, titles, and typed metadata only. Candidates are always
excluded. The sensitivity ceiling is enforced before anything is written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .graph_surface import build_graph_projection
from .vault import is_initialized

_FORMATS = {"json", "ndjson"}
_CEILINGS = {"public", "internal", "confidential", "restricted"}
_SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


class GraphExportError(RuntimeError):
    """Raised when graph export fails closed."""


def _manifest_hash(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True).encode("utf-8")
    ).hexdigest()


def export_graph(
    root: Path | str,
    out: Path | str,
    *,
    format: str = "json",
    sensitivity: str = "internal",
    entity: str | None = None,
) -> dict[str, Any]:
    """Export the canonical graph projection to JSON or NDJSON + receipt."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise GraphExportError("vault is not initialized")
    if format not in _FORMATS:
        raise GraphExportError(f"unknown export format: {format}")
    if sensitivity not in _CEILINGS:
        raise GraphExportError(f"unknown sensitivity ceiling: {sensitivity}")

    projection = build_graph_projection(
        vault,
        entity_id=entity,
        sensitivity_ceiling=sensitivity,
        include_candidates=False,
    )
    ceiling = _SENSITIVITY_RANK[sensitivity]
    # Count what the ceiling hides: rebuild at the highest ceiling and diff.
    unrestricted = build_graph_projection(
        vault,
        entity_id=entity,
        sensitivity_ceiling="restricted",
        include_candidates=False,
    )
    excluded_nodes = len(unrestricted["nodes"]) - len(projection["nodes"])
    nodes = [
        {
            "id": str(node["id"]),
            "title": str(node.get("title", "")),
            "type": str(node.get("type", "")),
            "sensitivity": str(node.get("sensitivity", "internal")),
        }
        for node in projection["nodes"]
    ]
    node_ids = {node["id"] for node in nodes}
    excluded_by_sensitivity = 0
    edges: list[dict[str, Any]] = []
    for edge in projection["edges"]:
        if _SENSITIVITY_RANK.get(str(edge.get("sensitivity", "internal")), 3) > ceiling:
            excluded_by_sensitivity += 1
            continue
        if str(edge["subject_id"]) not in node_ids or str(edge["object_id"]) not in node_ids:
            excluded_by_sensitivity += 1
            continue
        edges.append(
            {
                "edge_id": str(edge.get("edge_id", "")),
                "edge_kind": str(edge["edge_kind"]),
                "subject_id": str(edge["subject_id"]),
                "predicate": str(edge["predicate"]),
                "object_id": str(edge["object_id"]),
                "evidence_class": str(edge.get("evidence_class", "")),
                "confidence": edge.get("confidence"),
                "source_ids": [str(s) for s in (edge.get("source_ids") or [])],
                "sensitivity": str(edge.get("sensitivity", "internal")),
                "valid_from": edge.get("valid_from"),
                "valid_to": edge.get("valid_to"),
            }
        )
    nodes.sort(key=lambda node: node["id"])
    edges.sort(key=lambda edge: edge["edge_id"])
    excluded_total = excluded_by_sensitivity + excluded_nodes
    manifest = _manifest_hash(nodes, edges)

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    exported_at = datetime.now(UTC).isoformat()
    if format == "json":
        payload = {
            "version": 1,
            "exported_at": exported_at,
            "sensitivity": sensitivity,
            "entity_scope": entity,
            "manifest_hash": manifest,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        }
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        lines = [json.dumps({"kind": "node", **node}, sort_keys=True) for node in nodes]
        lines += [json.dumps({"kind": "edge", **edge}, sort_keys=True) for edge in edges]
        lines.append(json.dumps({
            "kind": "manifest", "version": 1, "exported_at": exported_at,
            "manifest_hash": manifest, "node_count": len(nodes), "edge_count": len(edges),
        }, sort_keys=True))
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    receipt = {
        "version": 1,
        "exported_at": exported_at,
        "output_path": str(out_path),
        "format": format,
        "sensitivity": sensitivity,
        "entity_scope": entity,
        "manifest_hash": manifest,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "excluded_by_sensitivity": excluded_total,
        "excluded_edges": excluded_by_sensitivity,
        "excluded_nodes": excluded_nodes,
        "candidates_excluded": True,
        "sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
    }
    receipt_path = out_path.with_suffix(".receipt.json")
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "ok",
        "output_path": str(out_path),
        "receipt_path": str(receipt_path),
        "manifest_hash": manifest,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "excluded_by_sensitivity": excluded_total,
    }
