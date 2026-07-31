"""Deterministic typology detection from graph shape (Wave 4 Task 4.1).

Detects investigation-relevant shapes over canonical relationships only —
never over claims, candidates, or co-occurrence. Every match carries the
member record IDs, the explaining edge IDs, and the source IDs so a
reviewer can verify the shape against evidence. A typology match is an
investigative lead: it labels nothing without evidence and never promotes
to canonical fact.

Initial shapes:

- ``layered_ownership``: a maximal owns chain of ≥2 edges across ≥3
  distinct entities (A owns B owns C);
- ``circular_ownership``: a directed owns cycle of length ≥3;
- ``shared_intermediary``: ≥2 distinct subjects owning the same
  intermediary;
- ``multi_hop_convergence``: ≥2 distinct directed 2–3 hop paths between
  the same entity pair (any canonical predicate).

Read-only and deterministic: same vault bytes → same report, no writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .predicates import default_registry
from .vault import is_initialized

_MAX_MATCHES_PER_TYPE = 25
_MAX_PATH_DEPTH = 6
_MAX_ENUMERATED_PATHS = 200


class TypologyError(RuntimeError):
    """Raised when typology scanning fails closed."""


def _canonical_relationships(vault: Path) -> list[dict[str, Any]]:
    base = vault / "relationships"
    records: list[dict[str, Any]] = []
    if not base.is_dir():
        return records
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not metadata.get("id"):
            continue
        if str(metadata.get("status", "")) in {"stale", "superseded"}:
            continue
        records.append(metadata)
    return records


def _adjacency(
    records: list[dict[str, Any]], registry, *, owns_only: bool
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for record in records:
        predicate = str(record.get("predicate", ""))
        resolution = registry.resolve(predicate) if registry else None
        canonical = str(resolution.canonical) if resolution and resolution.status != "unknown" else predicate
        if owns_only and canonical != "owns":
            continue
        subject = str(record.get("subject_id", ""))
        obj = str(record.get("object_id", ""))
        if not subject or not obj or subject == obj:
            continue
        adjacency.setdefault(subject, []).append((obj, record))
    for edges in adjacency.values():
        edges.sort(key=lambda item: (item[0], str(item[1]["id"])))
    return adjacency


def _simple_paths(
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]],
    start: str,
    *,
    min_len: int,
    max_len: int,
) -> list[list[tuple[str, str]]]:
    """Enumerate simple directed paths from start as (edge_id, next) chains.

    Returns chains of (node, edge_record_id) steps; bounded for safety.
    """
    found: list[list[tuple[str, str]]] = []

    def _walk(node: str, visited: set[str], chain: list[tuple[str, str]]) -> None:
        if len(found) >= _MAX_ENUMERATED_PATHS:
            return
        if len(chain) >= max_len:
            return
        for next_node, record in adjacency.get(node, []):
            if next_node in visited:
                continue
            next_chain = [*chain, (next_node, str(record["id"]))]
            if len(next_chain) >= min_len:
                found.append(next_chain)
            _walk(next_node, visited | {next_node}, next_chain)

    _walk(start, {start}, [])
    return found


def _match(
    typology: str,
    member_ids: set[str],
    edge_ids: set[str],
    records_by_id: dict[str, dict[str, Any]],
    summary: str,
) -> dict[str, Any]:
    source_ids = sorted(
        {str(s) for edge_id in edge_ids for s in (records_by_id[edge_id].get("source_ids") or [])}
    )
    return {
        "typology": typology,
        "member_ids": sorted(member_ids),
        "edge_ids": sorted(edge_ids),
        "source_ids": source_ids,
        "summary": summary,
    }


def scan_typologies(root: Path | str) -> dict[str, Any]:
    """Scan canonical relationships for deterministic typology shapes."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise TypologyError("vault is not initialized")
    registry = default_registry()
    records = _canonical_relationships(vault)
    records_by_id = {str(r["id"]): r for r in records}
    owns = _adjacency(records, registry, owns_only=True)
    all_edges = _adjacency(records, registry, owns_only=False)

    matches: list[dict[str, Any]] = []
    truncated: dict[str, bool] = {}

    # 1. layered ownership: maximal owns chains of >= 2 edges
    starts = sorted(set(owns) - {obj for edges in owns.values() for obj, _ in edges})
    layered: list[tuple[str, list[tuple[str, str]]]] = []
    for start in starts:
        for path in _simple_paths(owns, start, min_len=2, max_len=_MAX_PATH_DEPTH):
            end = path[-1][0]
            if end not in owns:  # maximal: cannot extend
                layered.append((start, path))
    layered.sort(key=lambda item: (item[0], tuple(node for node, _ in item[1])))
    truncated["layered_ownership"] = len(layered) > _MAX_MATCHES_PER_TYPE
    for start, path in layered[:_MAX_MATCHES_PER_TYPE]:
        members = {start, *(node for node, _ in path)}
        matches.append(_match(
            "layered_ownership",
            members,
            {edge_id for _, edge_id in path},
            records_by_id,
            f"layered ownership chain across {len(members)} entities",
        ))

    # 2. circular ownership: simple directed cycles length >= 3
    cycles: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for start in sorted(owns):
        def _walk_cycle(node: str, visited: set[str], chain: list[tuple[str, str]]) -> None:
            if len(chain) >= _MAX_PATH_DEPTH:
                return
            for next_node, record in owns.get(node, []):
                if next_node == start and len(chain) >= 2:
                    cycle_chain = [*chain, (next_node, str(record["id"]))]
                    nodes = [start, *(n for n, _ in cycle_chain[:-1])]
                    # canonical rotation for dedupe
                    rotations = [tuple(nodes[i:] + nodes[:i]) for i in range(len(nodes))]
                    key = min(rotations)
                    cycles.setdefault(key, cycle_chain)
                elif next_node not in visited and next_node > start:
                    # only visit nodes greater than start to enumerate each cycle once
                    _walk_cycle(next_node, visited | {next_node}, [*chain, (next_node, str(record["id"]))])
        _walk_cycle(start, {start}, [])
    cycle_list = sorted(cycles.items(), key=lambda item: item[0])
    truncated["circular_ownership"] = len(cycle_list) > _MAX_MATCHES_PER_TYPE
    for key, chain in cycle_list[:_MAX_MATCHES_PER_TYPE]:
        matches.append(_match(
            "circular_ownership",
            set(key),
            {edge_id for _, edge_id in chain},
            records_by_id,
            f"circular ownership cycle across {len(key)} entities",
        ))

    # 3. shared intermediary: >= 2 distinct subjects own the same object
    by_object: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for subject, edges in owns.items():
        for obj, record in edges:
            by_object.setdefault(obj, []).append((subject, record))
    shared = sorted(
        (obj, subjects) for obj, subjects in by_object.items()
        if len({s for s, _ in subjects}) >= 2
    )
    truncated["shared_intermediary"] = len(shared) > _MAX_MATCHES_PER_TYPE
    for obj, subjects in shared[:_MAX_MATCHES_PER_TYPE]:
        members = {obj, *(s for s, _ in subjects)}
        matches.append(_match(
            "shared_intermediary",
            members,
            {str(record["id"]) for _, record in subjects},
            records_by_id,
            f"{len({s for s, _ in subjects})} owners share one intermediary",
        ))

    # 4. multi-hop convergence: >= 2 distinct directed 2-3 hop paths between same pair
    convergence: dict[tuple[str, str], list[list[tuple[str, str]]]] = {}
    for start in sorted(all_edges):
        for path in _simple_paths(all_edges, start, min_len=2, max_len=3):
            end = path[-1][0]
            key = (start, end)
            convergence.setdefault(key, []).append(path)
    convergent = sorted(
        (key, paths) for key, paths in convergence.items() if len(paths) >= 2
    )
    truncated["multi_hop_convergence"] = len(convergent) > _MAX_MATCHES_PER_TYPE
    for (start, end), paths in convergent[:_MAX_MATCHES_PER_TYPE]:
        edge_ids = {edge_id for path in paths for _, edge_id in path}
        matches.append(_match(
            "multi_hop_convergence",
            {start, end},
            edge_ids,
            records_by_id,
            f"{len(paths)} distinct {min(len(p) for p in paths)}-{max(len(p) for p in paths)} hop paths converge",
        ))

    type_order = {
        "layered_ownership": 0,
        "circular_ownership": 1,
        "shared_intermediary": 2,
        "multi_hop_convergence": 3,
    }
    matches.sort(key=lambda m: (type_order[m["typology"]], m["member_ids"], m["edge_ids"]))
    counts: dict[str, int] = {}
    for match in matches:
        counts[match["typology"]] = counts.get(match["typology"], 0) + 1
    return {
        "status": "ok",
        "matches": matches,
        "counts": counts,
        "truncated": truncated,
        "relationships_scanned": len(records),
    }
