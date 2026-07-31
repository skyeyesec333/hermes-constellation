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

from datetime import datetime
from pathlib import Path
from typing import Any

from .graph_surface import build_graph_projection

_MAX_HOPS_LIMIT = 8
_DIRECTIONS = {"both", "outgoing", "incoming", "directed"}


class GraphApiError(RuntimeError):
    """Raised when graph API arguments are invalid."""


def _edge_sort_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    return (str(edge["predicate"]), str(edge["record_id"]), str(edge["object_id"]))


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _validate_options(direction: str, as_of: datetime | None) -> None:
    if direction not in _DIRECTIONS:
        raise GraphApiError(f"unknown direction: {direction}")
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise GraphApiError("as_of must include a timezone")


def _filtered_edges(
    vault: Path | str,
    *,
    sensitivity_ceiling: str,
    kinds: set[str] | None,
    include_candidates: bool,
    predicates: set[str] | None = None,
    as_of: datetime | None = None,
    min_confidence: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
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
    if predicates is not None:
        edges = [e for e in edges if str(e.get("predicate", "")) in predicates]
    if min_confidence is not None:
        edges = [
            e
            for e in edges
            if e.get("confidence") is not None and float(e["confidence"]) >= min_confidence
        ]
    excluded_by_as_of = 0
    if as_of is not None:
        kept: list[dict[str, Any]] = []
        for edge in edges:
            if edge["edge_kind"] != "relationship":
                kept.append(edge)
                continue
            valid_from = _parse_iso(edge.get("valid_from"))
            valid_to = _parse_iso(edge.get("valid_to"))
            if valid_from is None and valid_to is None:
                kept.append({**edge, "temporal_status": "unknown"})
                continue
            if (valid_from is not None and valid_from > as_of) or (
                valid_to is not None and valid_to < as_of
            ):
                excluded_by_as_of += 1
                continue
            kept.append({**edge, "temporal_status": "active"})
        edges = kept
    return sorted(edges, key=_edge_sort_key), excluded_by_as_of


def graph_neighbors(
    vault: Path | str,
    node_id: str,
    *,
    kinds: set[str] | None = None,
    include_candidates: bool = True,
    sensitivity_ceiling: str = "internal",
    limit: int = 50,
    predicates: set[str] | None = None,
    direction: str = "both",
    as_of: datetime | None = None,
    min_confidence: float | None = None,
) -> dict[str, Any]:
    """Return typed edges touching one node, newest-schema first, bounded.

    Review-surface semantics: candidates included (flagged) by default.
    ``directed`` follows subject→object orientation (``outgoing`` here).
    """
    if not 1 <= limit <= 500:
        raise GraphApiError("limit must be between 1 and 500")
    _validate_options(direction, as_of)
    edges, excluded_by_as_of = _filtered_edges(
        vault,
        sensitivity_ceiling=sensitivity_ceiling,
        kinds=kinds,
        include_candidates=include_candidates,
        predicates=predicates,
        as_of=as_of,
        min_confidence=min_confidence,
    )
    if direction in {"outgoing", "directed"}:
        edges = [e for e in edges if str(e["subject_id"]) == node_id]
    elif direction == "incoming":
        edges = [e for e in edges if str(e["object_id"]) == node_id]
    touching = [e for e in edges if node_id in {e["subject_id"], e["object_id"]}]
    truncated = len(touching) > limit
    result: dict[str, Any] = {
        "status": "neighbors_found" if touching else "no_edges_found",
        "node_id": node_id,
        "edges": touching[:limit],
        "total_edges": len(touching),
        "truncated": truncated,
        "candidates_included": include_candidates,
        "sensitivity_ceiling": sensitivity_ceiling,
        "filters": {
            "predicates": sorted(predicates) if predicates else None,
            "direction": direction,
            "as_of": as_of.isoformat() if as_of else None,
            "min_confidence": min_confidence,
        },
    }
    if as_of is not None:
        result["excluded_by_as_of"] = excluded_by_as_of
    return result


def graph_path(
    vault: Path | str,
    start_node_id: str,
    end_node_id: str,
    *,
    max_hops: int = 4,
    kinds: set[str] | None = None,
    include_candidates: bool = False,
    sensitivity_ceiling: str = "internal",
    predicates: set[str] | None = None,
    direction: str = "both",
    as_of: datetime | None = None,
    min_confidence: float | None = None,
) -> dict[str, Any]:
    """Return one deterministic shortest typed-edge chain between two nodes.

    Evidence-chain semantics: candidates excluded by default.
    ``direction`` controls traversal: ``both`` (default) walks either side;
    ``directed``/``outgoing`` follow subject→object; ``incoming`` follows
    object→subject.
    """
    if not 1 <= max_hops <= _MAX_HOPS_LIMIT:
        raise GraphApiError(f"max_hops must be between 1 and {_MAX_HOPS_LIMIT}")
    if start_node_id == end_node_id:
        raise GraphApiError("start and end nodes must differ")
    _validate_options(direction, as_of)
    edges, excluded_by_as_of = _filtered_edges(
        vault,
        sensitivity_ceiling=sensitivity_ceiling,
        kinds=kinds,
        include_candidates=include_candidates,
        predicates=predicates,
        as_of=as_of,
        min_confidence=min_confidence,
    )

    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for edge in edges:
        if direction in {"directed", "outgoing"}:
            adjacency.setdefault(str(edge["subject_id"]), []).append((str(edge["object_id"]), edge))
        elif direction == "incoming":
            adjacency.setdefault(str(edge["object_id"]), []).append((str(edge["subject_id"]), edge))
        else:
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
                result: dict[str, Any] = {
                    "status": "path_found",
                    "path": next_chain,
                    "hops": len(next_chain),
                    "candidates_included": include_candidates,
                }
                if as_of is not None:
                    result["excluded_by_as_of"] = excluded_by_as_of
                return result
            queue.append((next_node, next_chain, seen | {next_node}))
    result = {
        "status": "no_path_found",
        "path": [],
        "hops": 0,
        "candidates_included": include_candidates,
    }
    if as_of is not None:
        result["excluded_by_as_of"] = excluded_by_as_of
    return result
