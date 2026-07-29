"""Wave 4 briefing render: cited markdown + offline HTML evidence briefings."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from constellation.frontmatter import render_frontmatter
from constellation.models import (
    Claim,
    Decision,
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


def _entity(vault: Path, title: str) -> EntityRecord:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title,
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", record)
    return record


def _source(vault: Path) -> SourceItem:
    record = SourceItem(
        id=generate_ulid(), type="source_item", title="BOI guide",
        status="active", sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"bytes").hexdigest(),
        original_path="Library/Files/boi.pdf", media_type="application/pdf",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "source-items", record)
    return record


def _briefing_vault(tmp_path: Path) -> tuple[Path, str, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    acme = _entity(vault, "Acme")
    regcorp = _entity(vault, "RegCorp")
    source = _source(vault)
    _write(vault, "relationships", RelationshipRecord(
        id=generate_ulid(), type="relationship", title="Acme works with RegCorp",
        status="active", sensitivity=Sensitivity.INTERNAL,
        subject_id=acme.id, predicate="works_with", object_id=regcorp.id,
        source_ids=[source.id], evidence_class="corroborated", confidence=0.9,
        created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "claims", Claim(
        id=generate_ulid(), title="Acme expanding in Thailand", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=acme.id,
        predicate="expanding_in", object_id=regcorp.id,
        source_ids=[source.id], confidence=0.8, created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "decisions", Decision(
        id=generate_ulid(), title="Prioritize Acme diligence", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=acme.id,
        decision="Run full diligence on Acme",
        source_ids=[source.id], created_at=NOW, updated_at=NOW,
    ))
    cand = Claim(
        id=generate_ulid(), title="Acme rumored layoffs", status="review-required",
        sensitivity=Sensitivity.INTERNAL, subject_id=acme.id,
        predicate="layoffs_at", object_id=acme.id,
        source_ids=[source.id], confidence=0.4, created_at=NOW, updated_at=NOW,
    )
    (vault / ".constellation/candidates").mkdir(parents=True, exist_ok=True)
    (vault / ".constellation/candidates" / f"claim-{cand.id}.json").write_text(
        json.dumps(json.loads(cand.model_dump_json()), indent=2) + "\n", encoding="utf-8"
    )
    return vault, acme.id, cand.id


def test_build_entity_briefing_sections(tmp_path: Path) -> None:
    from constellation.briefing import build_entity_briefing

    vault, entity_id, cand_id = _briefing_vault(tmp_path)
    briefing = build_entity_briefing(vault, entity_id)

    assert briefing["entity"]["title"] == "Acme"
    rels = briefing["relationships"]
    assert len(rels) == 1 and rels[0]["predicate"] == "works_with"
    assert rels[0]["other_title"] == "RegCorp"
    claims = briefing["claims"]
    assert len(claims) == 1
    assert claims[0]["title"] == "Acme expanding in Thailand"
    assert claims[0]["confidence"] == 0.8
    assert any(c.startswith("source-items/") for c in claims[0]["citations"])
    assert briefing["decisions"][0]["title"] == "Prioritize Acme diligence"
    cands = briefing["candidates"]
    assert len(cands) == 1 and cands[0]["candidate"] is True
    assert cands[0]["title"] == "Acme rumored layoffs"


def test_render_briefing_markdown_cited(tmp_path: Path) -> None:
    from constellation.briefing import build_entity_briefing, render_briefing_markdown

    vault, entity_id, _ = _briefing_vault(tmp_path)
    md = render_briefing_markdown(build_entity_briefing(vault, entity_id))

    assert "# Briefing: Acme" in md
    assert "works_with" in md and "RegCorp" in md
    assert "Acme expanding in Thailand" in md
    assert "0.8" in md
    assert "source-items/" in md
    assert "Prioritize Acme diligence" in md
    assert "CANDIDATE" in md
    assert "Acme rumored layoffs" in md


def test_render_briefing_html_offline(tmp_path: Path) -> None:
    from constellation.briefing import build_entity_briefing, render_briefing_html

    vault, entity_id, _ = _briefing_vault(tmp_path)
    html = render_briefing_html(build_entity_briefing(vault, entity_id))

    assert "Acme expanding in Thailand" in html
    assert "source-items/" in html
    assert "candidate" in html.lower()
    assert "<script" not in html
    assert 'src="http' not in html and 'href="http' not in html
    assert "cdn" not in html.lower()


def test_briefing_cli_writes_file(tmp_path: Path, capsys) -> None:
    from constellation.cli import main as cli_main

    vault, entity_id, _ = _briefing_vault(tmp_path)
    out = tmp_path / "briefing.html"
    rc = cli_main([
        "briefing", str(vault), entity_id, "--format", "html", "--out", str(out),
    ])
    assert rc == 0
    assert "Acme" in out.read_text(encoding="utf-8")

    out_md = tmp_path / "briefing.md"
    rc = cli_main(["briefing", str(vault), entity_id, "--out", str(out_md)])
    assert rc == 0
    assert "# Briefing: Acme" in out_md.read_text(encoding="utf-8")


def test_briefing_claim_without_object_via_citations(tmp_path: Path) -> None:
    """Real-vault pattern: claims with no object_id surface only as citation edges."""
    from constellation.briefing import build_entity_briefing

    vault, entity_id, _ = _briefing_vault(tmp_path)
    _source(vault)  # second source so the claim cites two source-items
    sources = sorted(p.stem for p in (vault / "source-items").glob("*.md"))
    _write(vault, "claims", Claim(
        id=generate_ulid(), title="Acme BOI incentives", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="receives_incentive", object_id=None, object_literal="BOI",
        source_ids=sources, confidence=0.75, created_at=NOW, updated_at=NOW,
    ))
    briefing = build_entity_briefing(vault, entity_id)
    titles = [c["title"] for c in briefing["claims"]]
    assert titles.count("Acme BOI incentives") == 1
    claim = [c for c in briefing["claims"] if c["title"] == "Acme BOI incentives"][0]
    assert len(claim["citations"]) == 2
    assert all(c.startswith("source-items/") for c in claim["citations"])


def test_briefing_respects_sensitivity_ceiling(tmp_path: Path) -> None:
    from constellation.briefing import build_entity_briefing

    vault, entity_id, _ = _briefing_vault(tmp_path)
    sources = list((vault / "source-items").glob("*.md"))
    source_id = sources[0].stem
    _write(vault, "claims", Claim(
        id=generate_ulid(), title="Secret Acme term sheet", status="active",
        sensitivity=Sensitivity.CONFIDENTIAL, subject_id=entity_id,
        predicate="term_sheet", object_id=entity_id,
        source_ids=[source_id], confidence=0.9, created_at=NOW, updated_at=NOW,
    ))
    open_brief = build_entity_briefing(vault, entity_id, sensitivity_ceiling="internal")
    titles = [c["title"] for c in open_brief["claims"]]
    assert "Secret Acme term sheet" not in titles
    full = build_entity_briefing(vault, entity_id, sensitivity_ceiling="confidential")
    titles = [c["title"] for c in full["claims"]]
    assert "Secret Acme term sheet" in titles
