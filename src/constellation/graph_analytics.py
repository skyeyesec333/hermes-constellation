"""SNA metrics over the deterministic graph model (i2-successor Wave 3 Task 3.2).

Computes degree/in/out, closeness, betweenness, directed PageRank, and weak
components from the cited projection via ``build_graph_model``. Exact on
graphs up to 500 nodes; above that, betweenness uses a seeded deterministic
sample (never wall-clock randomness) and the receipt marks the
approximation. Decision/opportunity edges are excluded from SNA by default
(along with citations, observations, and events).

Every run writes an immutable receipt under
``.constellation/analysis-results/`` — a derived artifact family separate
from mention scans — recording projection hash, filters, parameters, and
truncation so a reviewer can reproduce or challenge the result.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .graph_model import GraphModelError, build_graph_model
from .storage import atomic_write_text
from .vault import is_initialized

_EXACT_NODE_LIMIT = 500
_APPROX_SAMPLE = 100
_RECEIPT_DIR = ".constellation/analysis-results"


class GraphAnalyticsError(RuntimeError):
    """Raised when analytics cannot run (missing extra or invalid input)."""


def entity_component_size(
    root: Path | str, entity_id: str, *, sensitivity_ceiling: str = "internal"
) -> int | None:
    """Weak-component size containing one entity, or None when NetworkX is
    absent. Never raises for a missing optional dependency — offline
    surfaces (briefing) call this so they never import networkx themselves."""
    try:
        import networkx as nx

        model = build_graph_model(root, sensitivity_ceiling=sensitivity_ceiling)
    except Exception:  # noqa: BLE001 — optional dependency may be absent
        return None
    for component in nx.weakly_connected_components(model.simple):
        if entity_id in component:
            return len(component)
    return 1


def _round(value: float) -> float:
    return round(float(value), 6)


def compute_graph_analytics(
    root: Path | str,
    *,
    sensitivity_ceiling: str = "internal",
    include_claims: bool = False,
    include_candidates: bool = False,
    predicates: set[str] | None = None,
    as_of=None,
    top: int = 25,
) -> dict[str, Any]:
    """Compute SNA metrics and write a deterministic analysis receipt."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise GraphAnalyticsError("vault is not initialized")
    if not 1 <= top <= 500:
        raise GraphAnalyticsError("top must be between 1 and 500")
    try:
        model = build_graph_model(
            vault,
            sensitivity_ceiling=sensitivity_ceiling,
            include_claims=include_claims,
            include_candidates=include_candidates,
            predicates=predicates,
            as_of=as_of,
        )
    except GraphModelError as exc:
        raise GraphAnalyticsError(str(exc)) from exc

    import networkx as nx

    graph = model.simple
    node_count = graph.number_of_nodes()
    approximation = node_count > _EXACT_NODE_LIMIT
    in_degree = dict(graph.in_degree())
    out_degree = dict(graph.out_degree())
    if node_count:
        closeness = nx.closeness_centrality(graph)
        if approximation:
            betweenness = nx.betweenness_centrality(
                graph, k=min(_APPROX_SAMPLE, node_count), seed=42
            )
        else:
            betweenness = nx.betweenness_centrality(graph)
        pagerank = nx.pagerank(graph)
        components = sorted(
            (len(component) for component in nx.weakly_connected_components(graph)),
            reverse=True,
        )
    else:
        closeness, betweenness, pagerank, components = {}, {}, {}, []

    ranked = sorted(
        graph.nodes,
        key=lambda node: (-pagerank.get(node, 0.0), str(node)),
    )
    truncated = len(ranked) > top
    top_nodes = [
        {
            "node_id": str(node),
            "title": model.node_titles.get(str(node), str(node)),
            "kind": model.node_kinds.get(str(node), ""),
            "degree": int(in_degree.get(node, 0)) + int(out_degree.get(node, 0)),
            "in_degree": int(in_degree.get(node, 0)),
            "out_degree": int(out_degree.get(node, 0)),
            "closeness": _round(closeness.get(node, 0.0)),
            "betweenness": _round(betweenness.get(node, 0.0)),
            "pagerank": _round(pagerank.get(node, 0.0)),
        }
        for node in ranked[:top]
    ]

    parameters = {
        "sensitivity_ceiling": sensitivity_ceiling,
        "include_claims": include_claims,
        "include_candidates": include_candidates,
        "predicates": sorted(predicates) if predicates else None,
        "as_of": as_of.isoformat() if as_of else None,
        "top": top,
        "approximation_sample": _APPROX_SAMPLE if approximation else None,
        "networkx_version": model.networkx_version,
    }
    receipt = {
        "family": "sna-report",
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "projection_hash": model.projection_hash,
        "parameters": parameters,
        "node_count": node_count,
        "record_edge_count": model.edge_count_records,
        "collapsed_edge_count": graph.number_of_edges(),
        "approximation": approximation,
        "excluded_by_as_of": model.excluded_by_as_of,
        "truncated": truncated,
        "components": {"count": len(components), "largest": components[0] if components else 0},
        "top_nodes": top_nodes,
    }
    receipt_rel = (
        f"{_RECEIPT_DIR}/sna-report-{model.projection_hash[:12]}.json"
    )
    atomic_write_text(
        vault, receipt_rel, json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )

    return {
        "status": "ok",
        "node_count": node_count,
        "record_edge_count": model.edge_count_records,
        "collapsed_edge_count": graph.number_of_edges(),
        "approximation": approximation,
        "truncated": truncated,
        "components": receipt["components"],
        "parameters": parameters,
        "excluded_by_as_of": model.excluded_by_as_of,
        "projection_hash": model.projection_hash,
        "top_nodes": top_nodes,
        "receipt_path": receipt_rel,
    }
