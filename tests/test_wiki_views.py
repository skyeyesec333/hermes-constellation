"""Tests for canonical-derived wiki views."""

from datetime import UTC, datetime
from pathlib import Path

from constellation.frontmatter import render_frontmatter
from constellation.models import EntityKind, EntityRecord, Sensitivity, generate_ulid
from constellation.vault import initialize_vault
from constellation.wiki_views import build_entity_dossier, render_dossier_markdown

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _write(vault: Path, folder: str, record) -> None:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n"),
        encoding="utf-8",
    )


def _raw_claim(vault: Path, subject_id: str, predicate: str, obj: str,
               source_ids: list[str], status: str = "active") -> str:
    claim_id = generate_ulid()
    (vault / "claims" / f"{claim_id}.md").write_text(
        render_frontmatter({
            "schema_version": "0.1", "id": claim_id, "type": "claim",
            "title": f"{predicate}: {obj}", "status": status,
            "sensitivity": "internal", "subject_id": subject_id,
            "predicate": predicate, "object_literal": obj,
            "source_ids": source_ids,
            "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
        }, f"# {predicate}\n"),
        encoding="utf-8",
    )
    return claim_id


def _setup(tmp_path: Path) -> tuple[Path, str, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="TestCo",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", entity)
    source_id = generate_ulid()
    _raw_claim(vault, entity.id, "competes_in", "logistics", [source_id])
    return vault, entity.id, source_id


def test_dossier_cites_claims_with_sources_and_status(tmp_path: Path) -> None:
    vault, entity_id, source_id = _setup(tmp_path)

    dossier = build_entity_dossier(vault, entity_id)

    assert dossier["entity"]["title"] == "TestCo"
    claim = dossier["claims"][0]
    assert claim["predicate"] == "competes_in"
    assert claim["source_ids"] == [source_id]
    assert claim["status"] == "active"
    assert claim["record_path"].startswith("claims/")


def test_dossier_flags_contradictions_visibly(tmp_path: Path) -> None:
    vault, entity_id, source_id = _setup(tmp_path)
    _raw_claim(vault, entity_id, "competes_in", "fintech", [source_id])

    dossier = build_entity_dossier(vault, entity_id)

    assert dossier["contradictions"]
    assert "competes_in" in dossier["contradictions"][0]


def test_rendered_view_marks_generated_and_rebuildable(tmp_path: Path) -> None:
    vault, entity_id, _ = _setup(tmp_path)

    markdown = render_dossier_markdown(build_entity_dossier(vault, entity_id))

    assert "GENERATED VIEW" in markdown
    assert "not a canonical record" in markdown
    assert "dossier" in markdown  # rebuild command present
    assert "claims/" in markdown  # citations present


def test_rebuild_is_byte_deterministic(tmp_path: Path) -> None:
    vault, entity_id, _ = _setup(tmp_path)

    first = render_dossier_markdown(build_entity_dossier(vault, entity_id))
    second = render_dossier_markdown(build_entity_dossier(vault, entity_id))

    assert first == second


def test_dossier_cli_writes_to_views_not_canonical(tmp_path: Path) -> None:
    from constellation.cli import build_parser, run_action

    vault, entity_id, _ = _setup(tmp_path)
    canonical_before = {p for p in vault.rglob("*.md")}

    values = vars(build_parser().parse_args(["dossier", str(vault), entity_id]))
    result = run_action(str(values.pop("command")), values)

    assert result["status"] == "written"
    output = Path(result["output_path"])
    assert "views" in output.parts
    canonical_after = {p for p in vault.rglob("*.md")}
    assert canonical_after - canonical_before == {output}
