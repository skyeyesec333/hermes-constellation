"""Wave 4 slice 1 — typed graph projection extensions (RED).

Extends the existing projection beyond entities/people + relationship/claim
edges: typed nodes and edges for sources, decisions, observations, events,
and opportunities; confidence/freshness/anchor metadata; skipped-invalid
visibility; candidate packets visibly distinct; deterministic rebuild.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from constellation.frontmatter import render_frontmatter
from constellation.graph_surface import build_graph_projection, render_graph_surface
from constellation.models import (
    Claim,
    Decision,
    EntityKind,
    EntityRecord,
    Event,
    Observation,
    Opportunity,
    OpportunityStage,
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


def _vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="GraphCo",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", entity)
    return vault, entity.id


def _source(vault: Path) -> str:
    import hashlib

    record = SourceItem(
        id=generate_ulid(), type="source_item", title="Briefing PDF",
        status="active", sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"bytes").hexdigest(),
        original_path="Library/Files/briefing.pdf", media_type="application/pdf",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "source-items", record)
    return record.id


def _full_vault(tmp_path: Path) -> tuple[Path, str, str]:
    vault, entity_id = _vault(tmp_path)
    target = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="TargetCo",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", target)
    source_id = _source(vault)
    _write(vault, "claims", Claim(
        id=generate_ulid(), title="claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="partners_with", object_id=target.id,
        source_ids=[source_id], confidence=0.7, created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "decisions", Decision(
        id=generate_ulid(), title="decide", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        decision="Partner with GraphCo",
        source_ids=[source_id], created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "observations", Observation(
        id=generate_ulid(), title="obs", status="active",
        sensitivity=Sensitivity.INTERNAL, watchlist_id=generate_ulid(),
        snapshot_id=generate_ulid(), change_summary="content changed",
        entity_ids=[entity_id], source_ids=[], created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "events", Event(
        id=generate_ulid(), title="launch", status="active",
        sensitivity=Sensitivity.INTERNAL, entity_ids=[entity_id],
        event_date="2026-06-01", event_type="product_launch",
        description="Launched v2", source_ids=[source_id], created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "opportunities", Opportunity(
        id=generate_ulid(), title="opp", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_ids=[entity_id],
        stage=OpportunityStage.QUALIFYING,
        created_at=NOW, updated_at=NOW,
    ))
    return vault, entity_id, source_id


def test_projection_includes_all_typed_records(tmp_path: Path) -> None:
    vault, entity_id, source_id = _full_vault(tmp_path)

    projection = build_graph_projection(vault)

    node_types = {node["type"] for node in projection["nodes"]}
    assert {"company", "source_item", "decision", "observation", "event", "opportunity"} <= node_types
    edge_kinds = {edge["edge_kind"] for edge in projection["edges"]}
    assert {"decision", "observation", "event", "opportunity", "citation"} <= edge_kinds
    citation = [e for e in projection["edges"] if e["edge_kind"] == "citation"]
    assert any(e["object_id"] == source_id for e in citation)


def test_projection_edges_carry_confidence_freshness_anchor(tmp_path: Path) -> None:
    vault, entity_id, _ = _full_vault(tmp_path)

    projection = build_graph_projection(vault)

    claim_edge = [e for e in projection["edges"] if e["edge_kind"] == "claim"][0]
    assert claim_edge["confidence"] == 0.7
    assert claim_edge["updated_at"].startswith("2026-07-29")
    assert claim_edge["record_path"].startswith("claims/")
    decision_edge = [e for e in projection["edges"] if e["edge_kind"] == "decision"][0]
    assert decision_edge["predicate"] == "decided_about"
    assert decision_edge["updated_at"]
    for node in projection["nodes"]:
        assert node["record_path"].endswith(".md")
        assert node["updated_at"]


def test_projection_marks_candidates_distinctly(tmp_path: Path) -> None:
    vault, entity_id, source_id = _full_vault(tmp_path)
    target = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="CandTarget",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", target)
    candidate = Claim(
        id=generate_ulid(), title="candidate claim", status="review-required",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="plans", object_id=target.id,
        source_ids=[source_id], confidence=0.4, created_at=NOW, updated_at=NOW,
    )
    packet = json.loads(candidate.model_dump_json())
    (vault / ".constellation/candidates" / f"claim-{candidate.id}.json").write_text(
        json.dumps(packet, indent=2), encoding="utf-8"
    )

    projection = build_graph_projection(vault)

    candidate_edges = [e for e in projection["edges"] if e.get("candidate")]
    canonical_edges = [e for e in projection["edges"] if not e.get("candidate")]
    assert len(candidate_edges) == 1
    assert candidate_edges[0]["record_id"] == candidate.id
    assert all(not e["candidate"] for e in canonical_edges)


def test_projection_is_deterministic_across_rebuilds(tmp_path: Path) -> None:
    vault, _, _ = _full_vault(tmp_path)

    first = build_graph_projection(vault)
    second = build_graph_projection(vault)

    assert first == second


def test_projection_counts_skipped_invalid_records(tmp_path: Path) -> None:
    vault, entity_id, _ = _full_vault(tmp_path)
    (vault / "claims" / "broken.md").write_text("---\nnot: [valid\n---\n", encoding="utf-8")

    projection = build_graph_projection(vault)

    assert projection["skipped_invalid_records"] >= 1
    assert projection["total_edges"] > 0


def test_render_distinguishes_candidates_and_stays_offline(tmp_path: Path) -> None:
    vault, entity_id, source_id = _full_vault(tmp_path)
    target = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="CandTarget2",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", target)
    candidate = Claim(
        id=generate_ulid(), title="candidate claim", status="review-required",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="plans", object_id=target.id,
        source_ids=[source_id], confidence=0.4, created_at=NOW, updated_at=NOW,
    )
    (vault / ".constellation/candidates" / f"claim-{candidate.id}.json").write_text(
        candidate.model_dump_json(indent=2), encoding="utf-8"
    )

    projection = build_graph_projection(vault)
    page = render_graph_surface(projection)

    assert "candidate" in page
    assert "0.4" in page or "confidence" in page
    assert "<script" not in page
    assert 'src="http' not in page and 'href="http' not in page


def test_layout_projection_positions_all_nodes_deterministically(tmp_path: Path) -> None:
    from constellation.graph_surface import layout_projection

    vault, _, _ = _full_vault(tmp_path)
    projection = build_graph_projection(vault)

    positions = layout_projection(projection)
    positioned_node_ids = {n["id"] for n in projection["nodes"]}
    assert set(positions) == positioned_node_ids
    for x, y in positions.values():
        # circular layout, centre 450,450 radius 340
        assert 100 <= x <= 800 and 100 <= y <= 800

    again = layout_projection(projection)
    assert again == positions


def test_render_uses_layout_projection_positions(tmp_path: Path) -> None:
    """The HTML render and the API layout must be the same geometry."""
    from constellation.graph_surface import layout_projection

    vault, _, _ = _full_vault(tmp_path)
    projection = build_graph_projection(vault)
    page = render_graph_surface(projection)

    x, y = layout_projection(projection)[projection["nodes"][0]["id"]]
    assert f'cx="{x:.1f}" cy="{y:.1f}"' in page
