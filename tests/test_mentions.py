"""Tests for evidence-anchored mention cross-reference (Wave 2 Task 2.3)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.mentions import (
    MentionError,
    scan_source_mentions,
    stage_mention_lead,
)
from constellation.models import (
    EntityKind,
    EntityRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.review import PromotionError, list_candidates, promote_candidate
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _write(vault: Path, folder: str, record, body: str = "# note\n") -> None:
    target = vault / folder / f"{record.id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), body),
        encoding="utf-8",
    )


def _entity(vault: Path, title: str, kind=EntityKind.COMPANY, aliases=None,
            sensitivity=Sensitivity.INTERNAL) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=kind, title=title, status="active",
        sensitivity=sensitivity, source_ids=[], aliases=list(aliases or []),
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "people" if kind == EntityKind.PERSON else "entities", record)
    return record.id


def _source(vault: Path, body: str, sensitivity=Sensitivity.INTERNAL) -> str:
    record = SourceItem(
        id=generate_ulid(), type="source_item", title="Fictional article",
        status="active", sensitivity=sensitivity,
        source_hash=hashlib.sha256(body.encode()).hexdigest(),
        original_path="Library/Files/article.txt", media_type="text/plain",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "source-items", record, body)
    return record.id


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_vault(root)
    return root


def test_scan_finds_title_and_alias_hits(vault: Path) -> None:
    alpha = _entity(vault, "Alpha Group", aliases=["Alpha"])
    source = _source(vault, "# Article\n\nAlpha Group signed the accord. Alpha led talks.\n")

    result = scan_source_mentions(vault, source)

    assert result["status"] == "ok"
    entity_ids = {tuple(hit["entity_ids"]) for hit in result["hits"]}
    assert (alpha,) in entity_ids
    methods = {hit["match_method"] for hit in result["hits"]}
    assert "title_exact" in methods
    assert "alias_exact" in methods
    for hit in result["hits"]:
        assert hit["anchor"].startswith("chars ")
    assert (vault / result["receipt_path"]).is_file()


def test_overlapping_aliases_longest_match_first(vault: Path) -> None:
    _entity(vault, "Beta", aliases=["Alpha"])
    alpha_group = _entity(vault, "Alpha Group")
    source = _source(vault, "# Article\n\nAlpha Group announced results.\n")

    result = scan_source_mentions(vault, source)

    matched = [(hit["surface"], tuple(hit["entity_ids"])) for hit in result["hits"]]
    assert ("Alpha Group", (alpha_group,)) in matched
    # The shorter overlapping alias must not produce a competing hit at the same span.
    spans = [hit["anchor"] for hit in result["hits"]]
    assert len(spans) == len(set(spans))


def test_ambiguous_person_names_reported(vault: Path) -> None:
    first = _entity(vault, "John Smith", EntityKind.PERSON)
    second = _entity(vault, "John Smith", EntityKind.PERSON)
    source = _source(vault, "# Article\n\nJohn Smith attended.\n")

    result = scan_source_mentions(vault, source)

    ambiguous = [hit for hit in result["hits"] if hit["ambiguous"]]
    assert len(ambiguous) == 1
    assert set(ambiguous[0]["entity_ids"]) == {first, second}
    assert result["ambiguous_count"] == 1


def test_title_inside_larger_word_does_not_match(vault: Path) -> None:
    _entity(vault, "Acme")
    source = _source(vault, "# Article\n\nAcmeCorp expanded. Acme stayed flat.\n")

    result = scan_source_mentions(vault, source)

    assert len(result["hits"]) == 1
    assert result["hits"][0]["surface"] == "Acme"


def test_sensitivity_ceiling_excludes_and_counts(vault: Path) -> None:
    _entity(vault, "OpenCo")
    _entity(vault, "HiddenCo", sensitivity=Sensitivity.RESTRICTED)
    source = _source(vault, "# Article\n\nOpenCo and HiddenCo met.\n", sensitivity=Sensitivity.INTERNAL)

    result = scan_source_mentions(vault, source)

    surfaces = {hit["surface"] for hit in result["hits"]}
    assert "OpenCo" in surfaces
    assert "HiddenCo" not in surfaces
    assert result["excluded_by_sensitivity"] >= 1


def test_no_body_text_in_output(vault: Path) -> None:
    marker = "zxqwv-private-body-marker-4421"
    _entity(vault, "OpenCo")
    source = _source(vault, f"# Article\n\nOpenCo did things. {marker}\n")

    result = scan_source_mentions(vault, source)

    assert marker not in json.dumps(result)
    receipt = json.loads((vault / result["receipt_path"]).read_text(encoding="utf-8"))
    assert marker not in json.dumps(receipt)


def test_co_mentions_are_leads_never_relationships(vault: Path) -> None:
    first = _entity(vault, "OpenCo")
    second = _entity(vault, "BetaCo")
    source = _source(vault, "# Article\n\nOpenCo and BetaCo met in Geneva.\n")

    result = scan_source_mentions(vault, source)

    assert result["co_mentions"]
    pair = result["co_mentions"][0]
    assert set(pair["entity_ids"]) == {first, second}
    # No relationship candidate or canonical edge is created by a scan.
    assert list((vault / "relationships").glob("*.md")) == []
    candidates = vault / ".constellation" / "candidates"
    assert not candidates.exists() or list(candidates.glob("*.json")) == []


def test_warninglist_suppression_is_visible(vault: Path) -> None:
    _entity(vault, "Unknown Entity")
    source = _source(vault, "# Article\n\nUnknown Entity was cited.\n")

    result = scan_source_mentions(vault, source)

    assert result["hits"] == []
    assert len(result["suppressed"]) == 1
    assert result["suppressed"][0]["reason"]


def test_scan_is_deterministic(vault: Path) -> None:
    _entity(vault, "Alpha Group", aliases=["Alpha"])
    source = _source(vault, "# Article\n\nAlpha Group signed. Alpha led.\n")

    first = scan_source_mentions(vault, source)
    second = scan_source_mentions(vault, source)

    drop = ("receipt_path",)
    assert {k: v for k, v in first.items() if k not in drop} == {
        k: v for k, v in second.items() if k not in drop
    }


def test_stage_mention_lead_is_review_only(vault: Path) -> None:
    entity = _entity(vault, "OpenCo")
    source = _source(vault, "# Article\n\nOpenCo expanded.\n")

    staged = stage_mention_lead(vault, source_id=source, entity_id=entity, anchor="chars 11-17")

    assert staged["status"] == "staged"
    packet = json.loads((vault / staged["candidate_path"]).read_text(encoding="utf-8"))
    assert packet["kind"] == "mention_candidate"
    listed = list_candidates(vault)
    mention = [c for c in listed if c["kind"] == "mention_candidate"]
    assert len(mention) == 1
    assert mention[0]["promotable"] is False
    with pytest.raises(PromotionError):
        promote_candidate(vault, Path(staged["candidate_path"]).stem,
                          confirm=True, expected_base_hash=None)
    # Rejected promotion leaves the packet in place.
    assert (vault / staged["candidate_path"]).is_file()


def test_stage_validates_references(vault: Path) -> None:
    entity = _entity(vault, "OpenCo")
    source = _source(vault, "# Article\n\nOpenCo expanded.\n")
    with pytest.raises(MentionError):
        stage_mention_lead(vault, source_id=generate_ulid(), entity_id=entity, anchor="chars 0-1")
    with pytest.raises(MentionError):
        stage_mention_lead(vault, source_id=source, entity_id=generate_ulid(), anchor="chars 0-1")
    with pytest.raises(MentionError):
        stage_mention_lead(vault, source_id=source, entity_id=entity, anchor="")
