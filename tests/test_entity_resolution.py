"""Entity resolution + source-family dedup (item 3, TDD).

Covers: slug-artifact title normalization, multi-signal duplicate detection
(normalized title, title-token subset, alias overlap, external_ids overlap),
keeper/stub selection, read-only source-family dedup reporting, and the
review-gated two-patch merge staging (never auto-merge).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.cli import main
from constellation.entity_resolution import (
    EntityResolutionError,
    entity_pair_id,
    normalize_entity_title,
    record_distinct_decision,
    scan_entity_duplicates,
    scan_source_family_duplicates,
    stage_merge_proposal,
)
from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.models import (
    EntityKind,
    EntityRecord,
    Sensitivity,
    SourceItem,
)
from constellation.storage import sha256_file
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 30, 3, 30, tzinfo=UTC)

KEEPER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
STUB_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
THIRD_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"


def invoke(capsys, *args):
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)["result"]


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    (vault / "people").mkdir(exist_ok=True)
    return vault


def _write_entity(
    vault: Path,
    *,
    entity_id: str,
    title: str,
    body: str,
    kind: EntityKind = EntityKind.COMPANY,
    folder: str = "entities",
    status: str = "active",
    aliases: list[str] | None = None,
    external_ids: dict[str, str] | None = None,
) -> EntityRecord:
    record = EntityRecord(
        id=entity_id,
        type=kind,
        title=title,
        status=status,
        sensitivity=Sensitivity.INTERNAL,
        aliases=aliases or [],
        external_ids=external_ids or {},
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / folder / f"{entity_id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), body),
        encoding="utf-8",
    )
    return record


def _write_source_item(
    vault: Path,
    *,
    item_id: str,
    source_hash: str,
    source_url: str | None,
    created_at: datetime = NOW,
) -> None:
    item = SourceItem(
        id=item_id,
        type="source-item",
        title=f"Fictional source {item_id[-4:]}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=created_at,
        updated_at=created_at,
        source_hash=source_hash,
        original_path=f"inbox/{item_id[-4:]}.txt",
        media_type="text/plain",
        source_url=source_url,
    )
    (vault / "source-items" / f"{item_id}.md").write_text(
        render_frontmatter(item.model_dump(mode="json", exclude_none=True), "Captured.\n"),
        encoding="utf-8",
    )


def _coreweave_pair(vault: Path) -> None:
    """Mirror of the real 2026-07-30 CoreWeave duplicate pair."""
    _write_entity(
        vault,
        entity_id=KEEPER_ID,
        title="company-billion-company-fictional-weave-inc",
        body="# Fictional Weave (NASDAQ: FCTV)\n\n" + "Enriched dossier content. " * 40,
    )
    _write_entity(
        vault,
        entity_id=STUB_ID,
        title="company-fictional-weave-inc",
        body="# Fictional Weave, Inc\n\n> auto-discovered stub. Needs enrichment.\n",
    )


# --- normalization -------------------------------------------------------


def test_normalize_strips_kind_prefix_and_legal_suffixes():
    assert normalize_entity_title("company-billion-company-fictional-weave-inc") == (
        "billion fictional weave"
    )
    assert normalize_entity_title("company-fictional-weave-inc") == "fictional weave"
    assert normalize_entity_title("Fictional Weave, Inc.") == "fictional weave"
    assert normalize_entity_title("Fictional Weave LLC") == "fictional weave"
    assert normalize_entity_title("person-ada-example") == "ada example"


def test_normalize_handles_casefold_and_punctuation():
    assert normalize_entity_title("  FICTORIAL   Weave — Holdings  ") == "fictorial weave holdings"
    assert normalize_entity_title("") == ""


# --- entity duplicate scan -----------------------------------------------


def test_scan_finds_slug_artifact_pair_and_picks_rich_keeper(tmp_path):
    vault = _vault(tmp_path)
    _coreweave_pair(vault)

    duplicates = scan_entity_duplicates(vault)

    assert len(duplicates) == 1
    dup = duplicates[0]
    assert dup.keeper_id == KEEPER_ID
    assert dup.stub_id == STUB_ID
    assert dup.proposed_title == "company-fictional-weave-inc"
    assert {signal.kind for signal in dup.signals} == {"title_subset"}


def test_scan_flags_exact_normalized_title_match(tmp_path):
    vault = _vault(tmp_path)
    _write_entity(
        vault, entity_id=KEEPER_ID, title="Fictional Weave, Inc.",
        body="# rich\n" + "x" * 400,
    )
    _write_entity(
        vault, entity_id=STUB_ID, title="company-fictional-weave-inc", body="# stub\n",
    )

    duplicates = scan_entity_duplicates(vault)

    assert len(duplicates) == 1
    assert duplicates[0].signals[0].kind == "title_exact"
    assert duplicates[0].keeper_id == KEEPER_ID


def test_scan_flags_alias_overlap(tmp_path):
    vault = _vault(tmp_path)
    _write_entity(
        vault, entity_id=KEEPER_ID, title="Fictional Weave Holdings",
        body="# rich\n" + "x" * 400, aliases=["Fictional Weave"],
    )
    _write_entity(
        vault, entity_id=STUB_ID, title="Fictional Weave", body="# stub\n",
    )

    duplicates = scan_entity_duplicates(vault)

    assert len(duplicates) == 1
    assert any(signal.kind == "alias" for signal in duplicates[0].signals)


def test_scan_flags_external_id_overlap(tmp_path):
    vault = _vault(tmp_path)
    _write_entity(
        vault, entity_id=KEEPER_ID, title="Fictional Weave",
        body="# rich\n" + "x" * 400, external_ids={"ticker": "FCTV"},
    )
    _write_entity(
        vault, entity_id=STUB_ID, title="FW Ventures",
        body="# stub\n", external_ids={"ticker": "fctv"},
    )

    duplicates = scan_entity_duplicates(vault)

    assert len(duplicates) == 1
    assert any(signal.kind == "external_id" for signal in duplicates[0].signals)


def test_scan_flags_exact_body_match_when_titles_are_unrelated(tmp_path):
    vault = _vault(tmp_path)
    shared_body = "# Fictional Executive\n\n" + "The same evidence-grade dossier. " * 12
    _write_entity(
        vault, entity_id=KEEPER_ID, title="person-headline-fragment", body=shared_body,
        kind=EntityKind.PERSON, folder="people",
    )
    _write_entity(
        vault, entity_id=STUB_ID, title="Ada Example", body=shared_body,
        kind=EntityKind.PERSON, folder="people",
    )

    duplicates = scan_entity_duplicates(vault)

    assert len(duplicates) == 1
    assert any(signal.kind == "body_exact" for signal in duplicates[0].signals)


def test_scan_skips_stale_and_merged_records(tmp_path):
    vault = _vault(tmp_path)
    _coreweave_pair(vault)
    _write_entity(
        vault, entity_id=THIRD_ID, title="company-fictional-weave-inc",
        body="# old stub\n", status="stale",
    )

    duplicates = scan_entity_duplicates(vault)

    assert len(duplicates) == 1
    assert {duplicates[0].keeper_id, duplicates[0].stub_id} == {KEEPER_ID, STUB_ID}


def test_scan_ignores_kind_mismatch_and_unrelated_entities(tmp_path):
    vault = _vault(tmp_path)
    _write_entity(
        vault, entity_id=KEEPER_ID, title="company-fictional-weave-inc",
        body="# company\n" + "x" * 300,
    )
    _write_entity(
        vault, entity_id=STUB_ID, title="person-fictional-weave", body="# person\n",
        kind=EntityKind.PERSON, folder="people",
    )
    _write_entity(
        vault, entity_id=THIRD_ID, title="Unrelated Systems Group", body="# other\n",
    )

    assert scan_entity_duplicates(vault) == []


def test_scan_skips_retired_records(tmp_path):
    vault = _vault(tmp_path)
    shared_body = "Retired extraction artifact. " * 20
    _write_entity(
        vault, entity_id=KEEPER_ID, title="artifact-one", body=shared_body,
        status="retired",
    )
    _write_entity(
        vault, entity_id=STUB_ID, title="artifact-two", body=shared_body,
        status="retired",
    )

    assert scan_entity_duplicates(vault) == []


def test_scan_is_deterministic(tmp_path):
    vault = _vault(tmp_path)
    _coreweave_pair(vault)

    assert scan_entity_duplicates(vault) == scan_entity_duplicates(vault)


def _distinct_fund_pair(vault):
    _write_entity(
        vault, entity_id=KEEPER_ID, title="Mid Cap Value Fund",
        body="# Generic fund category\n" + "x" * 200,
    )
    _write_entity(
        vault, entity_id=STUB_ID, title="PGIM Mid Cap Value Fund",
        body="# Named PGIM product\n" + "y" * 200,
    )


def _review_distinct_fund_pair(vault):
    return record_distinct_decision(
        vault,
        left_id=KEEPER_ID,
        right_id=STUB_ID,
        reason="Named PGIM product is distinct from the generic category record.",
        reviewed_by="owner-remediation",
        reviewed_at=NOW,
    )


def test_entity_pair_id_is_direction_independent():
    assert entity_pair_id(KEEPER_ID, STUB_ID) == entity_pair_id(STUB_ID, KEEPER_ID)


def test_distinct_decision_is_attached_to_scanned_pair(tmp_path):
    vault = _vault(tmp_path)
    _distinct_fund_pair(vault)
    _review_distinct_fund_pair(vault)

    duplicate = scan_entity_duplicates(vault)[0]

    assert duplicate.pair_id == entity_pair_id(KEEPER_ID, STUB_ID)
    assert duplicate.review is not None
    assert duplicate.review.decision == "distinct"


def test_cli_scan_separates_reviewed_distinct_pair(tmp_path, capsys):
    vault = _vault(tmp_path)
    _distinct_fund_pair(vault)
    _review_distinct_fund_pair(vault)

    result = invoke(capsys, "resolve", str(vault), "scan")

    assert result["duplicates"] == []
    assert len(result["reviewed_distinct"]) == 1
    assert result["untriaged_count"] == 0


def test_scan_requires_initialized_vault(tmp_path):
    with pytest.raises(EntityResolutionError):
        scan_entity_duplicates(tmp_path / "nowhere")


# --- source-family dedup --------------------------------------------------


def test_source_family_scan_groups_normalized_urls_and_hashes(tmp_path):
    vault = _vault(tmp_path)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    hash_a = "a" * 64
    hash_b = "b" * 64
    _write_source_item(
        vault, item_id=KEEPER_ID, source_hash=hash_a,
        source_url="https://Fictional.example/entry/", created_at=earlier,
    )
    _write_source_item(
        vault, item_id=STUB_ID, source_hash=hash_b,
        source_url="https://fictional.example/entry",
    )
    _write_source_item(
        vault, item_id=THIRD_ID, source_hash=hash_b,
        source_url="https://fictional.example/other",
    )

    families = scan_source_family_duplicates(vault)

    by_basis = {family.basis: family for family in families}
    assert set(by_basis) == {"source_url", "source_hash"}
    url_family = by_basis["source_url"]
    assert url_family.keeper_path == f"source-items/{KEEPER_ID}.md"
    assert url_family.duplicate_paths == (f"source-items/{STUB_ID}.md",)
    hash_family = by_basis["source_hash"]
    assert hash_family.keeper_path == f"source-items/{STUB_ID}.md"
    assert hash_family.duplicate_paths == (f"source-items/{THIRD_ID}.md",)


def test_source_family_scan_reports_nothing_for_unique_sources(tmp_path):
    vault = _vault(tmp_path)
    _write_source_item(
        vault, item_id=KEEPER_ID, source_hash="a" * 64,
        source_url="https://fictional.example/one",
    )
    _write_source_item(
        vault, item_id=STUB_ID, source_hash="b" * 64,
        source_url="https://fictional.example/two",
    )

    assert scan_source_family_duplicates(vault) == []


def test_source_family_scan_skips_superseded_items(tmp_path):
    vault = _vault(tmp_path)
    _write_source_item(
        vault, item_id=KEEPER_ID, source_hash="a" * 64,
        source_url="https://fictional.example/report",
    )
    _write_source_item(
        vault, item_id=STUB_ID, source_hash="a" * 64,
        source_url="https://fictional.example/report",
    )
    path = vault / "source-items" / f"{STUB_ID}.md"
    metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    metadata["status"] = "superseded"
    path.write_text(render_frontmatter(metadata, body), encoding="utf-8")

    assert scan_source_family_duplicates(vault) == []


# --- review-gated merge staging ------------------------------------------


def test_stage_merge_proposal_writes_two_review_candidates(tmp_path):
    vault = _vault(tmp_path)
    _coreweave_pair(vault)

    receipt = stage_merge_proposal(vault, keeper_id=KEEPER_ID, stub_id=STUB_ID)

    assert receipt["status"] == "staged"
    keeper_patch = (vault / ".constellation/candidates" / f"{receipt['keeper_candidate_id']}.json")
    stub_patch = (vault / ".constellation/candidates" / f"{receipt['stub_candidate_id']}.json")
    assert keeper_patch.is_file() and stub_patch.is_file()

    keeper_content = json.loads(keeper_patch.read_text(encoding="utf-8"))
    assert keeper_content["target_path"] == f"entities/{KEEPER_ID}.md"
    assert keeper_content["expected_base_hash"] == sha256_file(vault / "entities" / f"{KEEPER_ID}.md")
    metadata, _ = parse_frontmatter(keeper_content["content"])
    assert metadata["title"] == "company-fictional-weave-inc"
    assert "company-billion-company-fictional-weave-inc" in metadata["aliases"]
    assert metadata["status"] == "active"

    stub_content = json.loads(stub_patch.read_text(encoding="utf-8"))
    stub_meta, stub_body = parse_frontmatter(stub_content["content"])
    assert stub_meta["status"] == "stale"
    assert stub_meta["resolution_state"] == "merged"
    assert stub_meta["merged_into"] == KEEPER_ID
    assert f"duplicate of [[{KEEPER_ID}]]" in stub_body
    assert "retained for provenance" in stub_body


def test_stage_merge_proposal_preserves_stub_title_and_legacy_path_alias(tmp_path):
    vault = _vault(tmp_path)
    _write_entity(
        vault, entity_id=KEEPER_ID, title="Apoorva Mehta",
        body="# Apoorva Mehta\n\n" + "Evidence-grade dossier. " * 20,
        kind=EntityKind.PERSON, folder="people",
    )
    _write_entity(
        vault, entity_id=STUB_ID, title="Instacart call-shots executive",
        body="# Stub\n",
        kind=EntityKind.PERSON, folder="people",
    )
    stub_path = vault / "people" / f"{STUB_ID}.md"
    legacy_stub_path = vault / "people" / "person-call-shots-instacart.md"
    stub_path.rename(legacy_stub_path)

    receipt = stage_merge_proposal(vault, keeper_id=KEEPER_ID, stub_id=STUB_ID)

    keeper_packet = json.loads(
        (vault / ".constellation/candidates" / f"{receipt['keeper_candidate_id']}.json")
        .read_text(encoding="utf-8")
    )
    metadata, _ = parse_frontmatter(keeper_packet["content"])
    aliases = metadata["aliases"]
    assert isinstance(aliases, list)
    assert "Instacart call-shots executive" in aliases
    assert "call shots instacart" in aliases


def test_stage_merge_proposal_validates_pair(tmp_path):
    vault = _vault(tmp_path)
    _coreweave_pair(vault)

    with pytest.raises(EntityResolutionError):
        stage_merge_proposal(vault, keeper_id=KEEPER_ID, stub_id=KEEPER_ID)
    with pytest.raises(EntityResolutionError):
        stage_merge_proposal(vault, keeper_id=KEEPER_ID, stub_id=THIRD_ID)


def test_stage_merge_proposal_refuses_stale_stub_and_kind_mismatch(tmp_path):
    vault = _vault(tmp_path)
    _coreweave_pair(vault)
    _write_entity(
        vault, entity_id=THIRD_ID, title="person-fictional-weave", body="# p\n",
        kind=EntityKind.PERSON, folder="people",
    )

    with pytest.raises(EntityResolutionError):
        stage_merge_proposal(vault, keeper_id=KEEPER_ID, stub_id=THIRD_ID)

    # stale stub is already resolved — refuse to re-stage
    (vault / "entities" / f"{STUB_ID}.md").write_text(
        render_frontmatter(
            {**parse_frontmatter((vault / "entities" / f"{STUB_ID}.md").read_text())[0],
             "status": "stale"},
            "# stub\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(EntityResolutionError):
        stage_merge_proposal(vault, keeper_id=KEEPER_ID, stub_id=STUB_ID)


def test_staging_does_not_mutate_canonical_records(tmp_path):
    vault = _vault(tmp_path)
    _coreweave_pair(vault)
    before = sha256_file(vault / "entities" / f"{KEEPER_ID}.md")

    stage_merge_proposal(vault, keeper_id=KEEPER_ID, stub_id=STUB_ID)

    assert sha256_file(vault / "entities" / f"{KEEPER_ID}.md") == before
    from constellation.validation import validate_vault

    report = validate_vault(vault)
    assert report["invalid"] == 0


# --- CLI surface -----------------------------------------------------------


def test_cli_resolve_scan_reports_duplicates_and_families(tmp_path, capsys):
    vault = _vault(tmp_path)
    _coreweave_pair(vault)

    result = invoke(capsys, "resolve", str(vault), "scan")

    assert result["status"] == "duplicates_found"
    assert len(result["duplicates"]) == 1
    assert result["duplicates"][0]["keeper_id"] == KEEPER_ID
    assert result["source_families"] == []


def test_cli_resolve_stage_stages_pair(tmp_path, capsys):
    vault = _vault(tmp_path)
    _coreweave_pair(vault)

    result = invoke(
        capsys, "resolve", str(vault), "stage",
        "--keeper-id", KEEPER_ID, "--stub-id", STUB_ID,
    )

    assert result["status"] == "staged"
    candidates = list((vault / ".constellation/candidates").iterdir())
    assert len(candidates) == 2
