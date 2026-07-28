"""Tests for the graph projection and offline review surface."""

from datetime import datetime, timezone
from pathlib import Path

from constellation.frontmatter import render_frontmatter
from constellation.graph_surface import build_graph_projection, render_graph_surface
from constellation.models import (
    Claim,
    EntityKind,
    EntityRecord,
    RelationshipRecord,
    Sensitivity,
    generate_ulid,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _write(vault: Path, folder: str, record) -> None:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n"),
        encoding="utf-8",
    )


def _entity(vault: Path, title: str, sensitivity=Sensitivity.INTERNAL) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title,
        status="active", sensitivity=sensitivity, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", record)
    return record.id


def _relationship(vault: Path, subject_id: str, object_id: str, source_id: str) -> None:
    record = RelationshipRecord(
        id=generate_ulid(), title="works with", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=subject_id,
        predicate="partners_with", object_id=object_id,
        source_ids=[source_id], evidence_class="user-asserted",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "relationships", record)


def test_projection_cites_canonical_and_claim_derived_edges(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    source = _entity(vault, "SourceCo")
    _relationship(vault, a, b, source)
    claim = Claim(
        id=generate_ulid(), title="Beta competes", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=b, object_id=a,
        predicate="competes_with", source_ids=[source],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "claims", claim)

    projection = build_graph_projection(vault)

    assert projection["degraded"] is False
    assert len(projection["nodes"]) == 2
    edge_sources = {(e["predicate"], e["edge_source"]) for e in projection["edges"]}
    assert ("partners_with", "canonical_relationship") in edge_sources
    assert ("competes_with", "derived_from_claim") in edge_sources
    for edge in projection["edges"]:
        assert edge["source_ids"]


def test_projection_enforces_sensitivity_ceiling(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    a = _entity(vault, "Alpha")
    secret = _entity(vault, "SecretCo", sensitivity=Sensitivity.CONFIDENTIAL)
    source = _entity(vault, "SourceCo")
    _relationship(vault, a, secret, source)

    projection = build_graph_projection(vault, sensitivity_ceiling="internal")

    titles = {node["title"] for node in projection["nodes"]}
    assert "SecretCo" not in titles
    assert projection["edges"] == []


def test_projection_reports_degraded_when_no_relationships(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _entity(vault, "Lonely")

    projection = build_graph_projection(vault)

    assert projection["degraded"] is True
    assert "no relationships" in projection["degraded_reason"]
    assert projection["edges"] == []


def test_rendered_surface_is_offline_and_cites_sources(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    source = _entity(vault, "SourceCo")
    _relationship(vault, a, b, source)

    projection = build_graph_projection(vault)
    html = render_graph_surface(projection)

    assert "http://" not in html and "https://" not in html.replace("https://www.w3.org", "")
    assert "<script src" not in html
    assert "Alpha" in html and "Beta" in html
    assert "partners_with" in html


def test_graph_surface_cli_writes_file_with_confirmation(tmp_path: Path) -> None:
    from constellation.cli import build_parser, run_action

    vault = tmp_path / "vault"
    initialize_vault(vault)
    a = _entity(vault, "Alpha")
    b = _entity(vault, "Beta")
    source = _entity(vault, "SourceCo")
    _relationship(vault, a, b, source)
    output = tmp_path / "graph.html"

    values = vars(
        build_parser().parse_args(
            ["graph-surface", str(vault), "--output", str(output)]
        )
    )
    result = run_action(str(values.pop("command")), values)

    assert result["status"] == "written"
    assert result["output_path"] == str(output)
    assert result["bytes_written"] == output.stat().st_size
    assert output.is_file()
    content = output.read_text(encoding="utf-8")
    assert "Alpha" in content


def test_graph_surface_cli_empty_vault_writes_visible_degraded_state(tmp_path: Path) -> None:
    from constellation.cli import build_parser, run_action

    vault = tmp_path / "vault"
    initialize_vault(vault)
    output = tmp_path / "graph.html"

    values = vars(
        build_parser().parse_args(
            ["graph-surface", str(vault), "--output", str(output)]
        )
    )
    result = run_action(str(values.pop("command")), values)

    assert result["status"] == "written"
    assert result["degraded"] is True
    content = output.read_text(encoding="utf-8")
    assert "Degraded" in content
