"""Wave 4 slice 2 — bounded neighbor/path/filter APIs over the typed projection (RED).

graph.py's neighbors/path only see canonical relationship records. These APIs
operate on the full typed projection (claims, citations, typed record edges,
candidates) with deterministic ordering and explicit bounds.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.graph_api import graph_neighbors, graph_path
from constellation.models import (
    Claim,
    EntityKind,
    EntityRecord,
    RelationshipRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _write(vault: Path, folder: str, record) -> None:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n"),
        encoding="utf-8",
    )


def _entity(vault: Path, title: str) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title,
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", record)
    return record.id


def _source(vault: Path) -> str:
    import hashlib

    record = SourceItem(
        id=generate_ulid(), type="source_item", title="Brief",
        status="active", sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"x").hexdigest(),
        original_path="Library/Files/b.pdf", media_type="application/pdf",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "source-items", record)
    return record.id


def _chain_vault(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A -rel-> B -claim-> C, A -citation-> S."""
    vault = tmp_path / "vault"
    initialize_vault(vault)
    ids = {"a": _entity(vault, "Alpha"), "b": _entity(vault, "Beta"), "c": _entity(vault, "Gamma")}
    ids["s"] = _source(vault)
    _write(vault, "relationships", RelationshipRecord(
        id=generate_ulid(), title="rel", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=ids["a"], object_id=ids["b"],
        predicate="partners_with", source_ids=[ids["s"]], evidence_class="user-asserted",
        created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "claims", Claim(
        id=generate_ulid(), title="claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=ids["b"], object_id=ids["c"],
        predicate="competes_with", source_ids=[ids["s"]], confidence=0.6,
        created_at=NOW, updated_at=NOW,
    ))
    return vault, ids


def test_neighbors_returns_all_typed_edges_touching_node(tmp_path: Path) -> None:
    vault, ids = _chain_vault(tmp_path)

    result = graph_neighbors(vault, ids["b"])

    assert result["status"] == "neighbors_found"
    kinds = {e["edge_kind"] for e in result["edges"]}
    assert kinds == {"relationship", "claim", "citation"}
    for edge in result["edges"]:
        assert edge["record_path"].endswith(".md")


def test_neighbors_filter_by_edge_kind(tmp_path: Path) -> None:
    vault, ids = _chain_vault(tmp_path)

    result = graph_neighbors(vault, ids["b"], kinds={"citation"})

    assert {e["edge_kind"] for e in result["edges"]} == {"citation"}


def test_neighbors_can_exclude_candidates(tmp_path: Path) -> None:
    vault, ids = _chain_vault(tmp_path)
    packet = Claim(
        id=generate_ulid(), title="cand", status="review-required",
        sensitivity=Sensitivity.INTERNAL, subject_id=ids["a"], object_id=ids["c"],
        predicate="eyeing", source_ids=[ids["s"]], created_at=NOW, updated_at=NOW,
    )
    (vault / ".constellation/candidates" / f"claim-{packet.id}.json").write_text(
        packet.model_dump_json(indent=2), encoding="utf-8"
    )

    with_candidates = graph_neighbors(vault, ids["a"])
    without = graph_neighbors(vault, ids["a"], include_candidates=False)

    assert any(e["candidate"] for e in with_candidates["edges"])
    assert all(not e["candidate"] for e in without["edges"])
    assert len(without["edges"]) < len(with_candidates["edges"])


def test_neighbors_unknown_node_is_explicit(tmp_path: Path) -> None:
    vault, _ = _chain_vault(tmp_path)

    result = graph_neighbors(vault, generate_ulid())

    assert result["status"] == "no_edges_found"
    assert result["edges"] == []


def test_path_traverses_typed_edges_deterministically(tmp_path: Path) -> None:
    vault, ids = _chain_vault(tmp_path)

    result = graph_path(vault, ids["a"], ids["c"])

    assert result["status"] == "path_found"
    chain = result["path"]
    assert len(chain) == 2
    assert chain[0]["subject_id"] == ids["a"]
    assert chain[1]["object_id"] == ids["c"]
    again = graph_path(vault, ids["a"], ids["c"])
    assert again == result


def test_path_respects_max_hops_bound(tmp_path: Path) -> None:
    vault, ids = _chain_vault(tmp_path)

    assert graph_path(vault, ids["a"], ids["c"], max_hops=1)["status"] == "no_path_found"
    with pytest.raises(Exception, match="max_hops"):
        graph_path(vault, ids["a"], ids["c"], max_hops=99)


def test_path_excludes_candidates_by_default(tmp_path: Path) -> None:
    vault, ids = _chain_vault(tmp_path)
    # only a candidate edge links a -> c directly; canonical path is via b
    packet = Claim(
        id=generate_ulid(), title="cand", status="review-required",
        sensitivity=Sensitivity.INTERNAL, subject_id=ids["a"], object_id=ids["c"],
        predicate="eyeing", source_ids=[ids["s"]], created_at=NOW, updated_at=NOW,
    )
    (vault / ".constellation/candidates" / f"claim-{packet.id}.json").write_text(
        packet.model_dump_json(indent=2), encoding="utf-8"
    )

    result = graph_path(vault, ids["a"], ids["c"])

    assert all(not e["candidate"] for e in result["path"])
    assert len(result["path"]) == 2  # via b, not the 1-hop candidate edge


def test_typed_graph_cli_neighbors_and_path(tmp_path: Path) -> None:
    from constellation.cli import build_parser, run_action

    vault, ids = _chain_vault(tmp_path)

    values = vars(build_parser().parse_args([
        "graph", str(vault), "neighbors", "--entity", ids["b"], "--typed",
    ]))
    result = run_action(str(values.pop("command")), values)
    assert result["status"] == "neighbors_found"
    assert {e["edge_kind"] for e in result["edges"]} >= {"relationship", "claim"}

    values = vars(build_parser().parse_args([
        "graph", str(vault), "path", "--from", ids["a"], "--to", ids["c"], "--typed",
    ]))
    result = run_action(str(values.pop("command")), values)
    assert result["status"] == "path_found"
    assert result["hops"] == 2
