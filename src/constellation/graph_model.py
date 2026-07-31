"""Deterministic in-memory graph analysis model (i2-successor Wave 3 Task 3.1).

Builds a NetworkX graph from the existing cited projection — never from
hidden state. Defaults: canonical relationships only, candidates excluded,
sensitivity ceiling enforced before graph creation. A ``MultiDiGraph`` is
retained for evidence inspection; a collapsed simple directed graph feeds
algorithms. Parallel records collapse into one topological edge carrying
``record_ids``, ``source_ids``, and ``evidence_count``. NetworkX weights
are deliberately not used for shortest paths in v1.

NetworkX is an optional dependency (``graph`` extra); everything here fails
with an actionable error when it is absent, and the rest of the graph
surface keeps working without it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


class GraphModelError(RuntimeError):
    """Raised when the graph model cannot be built (missing extra or bad input)."""


def _require_networkx():
    try:
        import networkx as nx
    except ImportError as exc:
        raise GraphModelError(
            "graph analytics require the optional networkx dependency; "
            "install with: pip install 'hermes-constellation[graph]'"
        ) from exc
    return nx


@dataclass(frozen=True)
class GraphModel:
    """Deterministic graph built from the cited projection."""

    multi: Any  # nx.MultiDiGraph — per-record evidence edges
    simple: Any  # nx.DiGraph — collapsed edges for algorithms
    node_titles: dict[str, str]
    node_kinds: dict[str, str]
    edge_count_records: int
    excluded_by_as_of: int
    filters: dict[str, Any]
    projection_hash: str
    networkx_version: str = field(default="")


def build_graph_model(
    root: Path | str,
    *,
    sensitivity_ceiling: str = "internal",
    include_claims: bool = False,
    include_candidates: bool = False,
    predicates: set[str] | None = None,
    as_of: datetime | None = None,
) -> GraphModel:
    """Build the deterministic analysis graph from the typed projection.

    Edge selection: canonical relationships by default; claim-derived
    entity edges only when ``include_claims``; candidates only when
    explicitly requested. Citations, decisions, observations, events, and
    opportunities are never SNA inputs in v1.
    """
    nx = _require_networkx()
    from .graph_api import _filtered_edges

    kinds = {"relationship", "claim"} if include_claims else {"relationship"}
    edges, excluded_by_as_of = _filtered_edges(
        root,
        sensitivity_ceiling=sensitivity_ceiling,
        kinds=kinds,
        include_candidates=include_candidates,
        predicates=predicates,
        as_of=as_of,
    )

    projection_hash = hashlib.sha256(
        json.dumps(edges, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    multi = nx.MultiDiGraph()
    simple = nx.DiGraph()
    node_titles: dict[str, str] = {}
    node_kinds: dict[str, str] = {}

    from .graph_surface import build_graph_projection

    projection = build_graph_projection(
        root, sensitivity_ceiling=sensitivity_ceiling, include_candidates=include_candidates
    )
    for node in projection["nodes"]:
        node_titles[str(node["id"])] = str(node.get("title", node["id"]))
        node_kinds[str(node["id"])] = str(node.get("type", ""))

    collapsed: dict[tuple[str, str], dict[str, Any]] = {}
    record_count = 0
    for edge in edges:
        subject = str(edge["subject_id"])
        obj = str(edge["object_id"])
        multi.add_edge(
            subject,
            obj,
            key=str(edge["record_id"]),
            record_id=str(edge["record_id"]),
            predicate=str(edge["predicate"]),
            edge_source=str(edge["edge_source"]),
            candidate=bool(edge["candidate"]),
        )
        record_count += 1
        bucket = collapsed.setdefault(
            (subject, obj),
            {"record_ids": [], "source_ids": set(), "predicates": set(), "evidence_count": 0},
        )
        bucket["record_ids"].append(str(edge["record_id"]))
        bucket["source_ids"].update(str(item) for item in (edge.get("source_ids") or []))
        bucket["predicates"].add(str(edge["predicate"]))
        bucket["evidence_count"] += 1

    for subject, obj in sorted(collapsed):
        bucket = collapsed[(subject, obj)]
        simple.add_edge(
            subject,
            obj,
            record_ids=sorted(bucket["record_ids"]),
            source_ids=sorted(bucket["source_ids"]),
            predicates=sorted(bucket["predicates"]),
            evidence_count=bucket["evidence_count"],
        )

    return GraphModel(
        multi=multi,
        simple=simple,
        node_titles=node_titles,
        node_kinds=node_kinds,
        edge_count_records=record_count,
        excluded_by_as_of=excluded_by_as_of,
        filters={
            "sensitivity_ceiling": sensitivity_ceiling,
            "include_claims": include_claims,
            "include_candidates": include_candidates,
            "predicates": sorted(predicates) if predicates else None,
            "as_of": as_of.isoformat() if as_of else None,
        },
        projection_hash=projection_hash,
        networkx_version=str(nx.__version__),
    )
