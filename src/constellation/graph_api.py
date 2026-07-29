"""Bounded neighbor/path/filter APIs over the typed graph projection.

Unlike graph.py (canonical relationship records only), these APIs traverse
the full typed projection — claims, citations, and typed record edges — via
graph_surface.build_graph_projection, so every result carries the
projection's citations, confidence, freshness, and candidate flags.

Traversal is deterministic: edges are processed in the projection's stable
sorted order, BFS queues are FIFO, and identical inputs always produce
identical chains.

Candidate policy differs by intent:
- ``graph_neighbors`` is a review surface: candidates are included by
  default (flagged), excludable via ``include_candidates=False``.
- ``graph_path`` derives chains of evidence: candidates are excluded by
  default — a review-required packet must never silently complete a chain.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .graph_surface import build_graph_projection

_MAX_HOPS_LIMIT = 8


class GraphApiError(RuntimeError):
    """Raised when graph API arguments are invalid."""


def _edge_sort_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    return (str(edge["predicate"]), str(edge["record_id"]), str(edge["object_id"]))


def _filtered_edges(
    vault: Path | str,
    *,
    sensitivity_ceiling: str,
    kinds: set[str] | None,
    include_candidates: bool,
) -> list[dict[str, Any]]:
    projection = build_graph_projection(
        vault,
        sensitivity_ceiling=sensitivity_ceiling,
        include_candidates=True,
    )
    edges = projection["edges"]
    if kinds is not None:
        unknown = kinds - {"relationship", "claim", "decision", "observation", "event", "opportunity", "citation"}
        if unknown:
            raise GraphApiError(f"unknown edge kinds: {sorted(unknown)}")
        edges = [e for e in edges if e["edge_kind"] in kinds]
    if not include_candidates:
        edges = [e for e in edges if not e["candidate"]]
    return sorted(edges, key=_edge_sort_key)


def graph_neighbors(
    vault: Path | str,
    node_id: str,
    *,
    kinds: set[str] | None = None,
    include_candidates: bool = True,
    sensitivity_ceiling: str = "internal",
    limit: int = 50,
) -> dict[str, Any]:
    """Return typed edges touching one node, newest-schema first, bounded.

    Review-surface semantics: candidates included (flagged) by default.
    """
    if not 1 <= limit <= 500:
        raise GraphApiError("limit must be between 1 and 500")
    edges = _filtered_edges(
        vault,
        sensitivity_ceiling=sensitivity_ceiling,
        kinds=kinds,
        include_candidates=include_candidates,
    )
    touching = [e for e in edges if node_id in {e["subject_id"], e["object_id"]}]
    truncated = len(touching) > limit
    return {
        "status": "neighbors_found" if touching else "no_edges_found",
        "node_id": node_id,
        "edges": touching[:limit],
        "total_edges": len(touching),
        "truncated": truncated,
        "candidates_included": include_candidates,
        "sensitivity_ceiling": sensitivity_ceiling,
    }


def graph_path(
    vault: Path | str,
    start_node_id: str,
    end_node_id: str,
    *,
    max_hops: int = 4,
    kinds: set[str] | None = None,
    include_candidates: bool = False,
    sensitivity_ceiling: str = "internal",
) -> dict[str, Any]:
    """Return one deterministic shortest typed-edge chain between two nodes.

    Evidence-chain semantics: candidates excluded by default.
    """
    if not 1 <= max_hops <= _MAX_HOPS_LIMIT:
        raise GraphApiError(f"max_hops must be between 1 and {_MAX_HOPS_LIMIT}")
    if start_node_id == end_node_id:
        raise GraphApiError("start and end nodes must differ")
    edges = _filtered_edges(
        vault,
        sensitivity_ceiling=sensitivity_ceiling,
        kinds=kinds,
        include_candidates=include_candidates,
    )

    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for edge in edges:
        adjacency.setdefault(str(edge["subject_id"]), []).append((str(edge["object_id"]), edge))
        adjacency.setdefault(str(edge["object_id"]), []).append((str(edge["subject_id"]), edge))

    queue: list[tuple[str, list[dict[str, Any]], frozenset[str]]] = [
        (start_node_id, [], frozenset({start_node_id}))
    ]
    while queue:
        current, chain, seen = queue.pop(0)
        if len(chain) >= max_hops:
            continue
        for next_node, edge in adjacency.get(current, []):
            if next_node in seen:
                continue
            next_chain = [*chain, edge]
            if next_node == end_node_id:
                return {
                    "status": "path_found",
                    "path": next_chain,
                    "hops": len(next_chain),
                    "candidates_included": include_candidates,
                }
            queue.append((next_node, next_chain, seen | {next_node}))
    return {
        "status": "no_path_found",
        "path": [],
        "hops": 0,
        "candidates_included": include_candidates,
    }
