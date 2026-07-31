"""Tests for the bounded FollowTheMoney exchange adapter (Wave 2 Task 2.1)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.ftm_adapter import (
    FtmAdapterError,
    ftm_export,
    ftm_import,
    load_ftm_profile,
    parse_ftm_ndjson,
)
from constellation.models import (
    EntityKind,
    EntityRecord,
    RelationshipRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.review import promote_candidate
from constellation.vault import initialize_vault

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "ftm" / "fictional-entities.ndjson"
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _write(vault: Path, folder: str, record, body: str = "# note\n") -> None:
    target = vault / folder / f"{record.id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), body),
        encoding="utf-8",
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_vault(root)
    return root


def test_parse_fixture_bounded(vault: Path) -> None:
    profile = load_ftm_profile()
    document = parse_ftm_ndjson(FIXTURE, profile=profile)
    assert document["entity_count"] == 4
    assert document["relationship_count"] == 5
    assert document["by_schema"]["Person"] == 1
    assert document["by_schema"]["Ownership"] == 1
    assert document["input_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


def test_dry_run_writes_nothing(vault: Path) -> None:
    result = ftm_import(vault, FIXTURE, stage=False)
    assert result["mode"] == "dry-run"
    assert result["entities"]["total"] == 4
    assert result["relationships"]["total"] == 5
    candidates = vault / ".constellation" / "candidates"
    assert not candidates.exists() or list(candidates.glob("*.json")) == []


def test_stage_entities_idempotent(vault: Path) -> None:
    first = ftm_import(vault, FIXTURE, stage=True)
    assert first["entities"]["staged"] == 4
    second = ftm_import(vault, FIXTURE, stage=True)
    assert second["entities"]["staged"] == 0
    assert second["entities"]["already_staged"] == 4
    candidates = list((vault / ".constellation" / "candidates").glob("*.json"))
    assert len(candidates) == 4
    # Person routes to people/, companies/organizations to entities/.
    folders = set()
    for path in candidates:
        payload = json.loads(path.read_text(encoding="utf-8"))
        folders.add(payload["target_path"].split("/")[0])
    assert folders == {"people", "entities"}
    # External FtM id is preserved through external_ids.
    sample = json.loads(candidates[0].read_text(encoding="utf-8"))
    metadata, _ = parse_frontmatter(sample["content"])
    assert metadata["external_ids"]["ftm"].startswith("ftm-")


def test_relationships_blocked_until_endpoints_and_proof_resolve(vault: Path) -> None:
    result = ftm_import(vault, FIXTURE, stage=True)
    blocked = {item["id"]: item["reason"] for item in result["relationships"]["blocked"]}
    # Nothing staged: endpoints are candidates, not canonical.
    assert result["relationships"]["staged"] == 0
    assert blocked["ftm-rel-001"] == "endpoint_not_canonical"
    assert blocked["ftm-rel-005"] == "self_relationship"


def test_relationships_stage_after_promotion_with_proof(vault: Path) -> None:
    ftm_import(vault, FIXTURE, stage=True)
    # Promote every entity candidate (owner action simulated in a disposable vault).
    for path in sorted((vault / ".constellation" / "candidates").glob("*.json")):
        promote_candidate(vault, path.stem, confirm=True, expected_base_hash=None)
    # Canonical source matching the proof URL of ftm-rel-001.
    _write(vault, "source-items", SourceItem(
        id=generate_ulid(), type="source_item", title="Fictional registry filing",
        status="active", sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"bytes").hexdigest(),
        original_path="Library/Files/filing.pdf", media_type="application/pdf",
        source_url="https://fictional-registry.example.test/filings/001",
        created_at=NOW, updated_at=NOW,
    ))

    result = ftm_import(vault, FIXTURE, stage=True)
    assert result["relationships"]["staged"] == 1
    blocked = {item["id"]: item["reason"] for item in result["relationships"]["blocked"]}
    assert blocked["ftm-rel-002"] == "unresolved_proof"
    assert blocked["ftm-rel-003"] == "unresolved_proof"
    assert blocked["ftm-rel-005"] == "self_relationship"

    # The staged relationship candidate carries temporal + qualifier mapping.
    rel_candidates = [
        p for p in (vault / ".constellation" / "candidates").glob("relationship-*.json")
    ]
    assert len(rel_candidates) == 1
    packet = json.loads(rel_candidates[0].read_text(encoding="utf-8"))
    record = packet["record"]
    assert record["predicate"] == "owns"
    assert record["valid_from"].startswith("2022-01-01")
    assert record["valid_to"].startswith("2024-06-30")
    assert record["qualifiers"]["percentage"] == "40"
    assert record["role"] == "beneficial owner"
    assert packet["extractor"]["name"] == "ftm-import"


def test_export_maps_canonical_subset(vault: Path, tmp_path: Path) -> None:
    person = EntityRecord(
        id=generate_ulid(), type=EntityKind.PERSON, title="Ada Fictional",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        external_ids={"ftm": "ftm-person-001"}, created_at=NOW, updated_at=NOW,
    )
    company = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="Fictional Holdings Ltd",
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    hidden_co = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title="Confidential Co",
        status="active", sensitivity=Sensitivity.CONFIDENTIAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "people", person)
    _write(vault, "entities", company)
    _write(vault, "entities", hidden_co)
    _write(vault, "relationships", RelationshipRecord(
        id=generate_ulid(), title="owns", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=person.id, object_id=company.id,
        predicate="owns", source_ids=[generate_ulid()], evidence_class="user-asserted",
        valid_from=datetime(2022, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2024, 6, 30, tzinfo=timezone.utc),
        role="beneficial owner", qualifiers={"percentage": "40"},
        created_at=NOW, updated_at=NOW,
    ))
    _write(vault, "relationships", RelationshipRecord(
        id=generate_ulid(), title="supplies", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=company.id, object_id=person.id,
        predicate="supplies", source_ids=[generate_ulid()], evidence_class="user-asserted",
        created_at=NOW, updated_at=NOW,
    ))
    out = tmp_path / "export.ndjson"

    result = ftm_export(vault, out, sensitivity="internal")

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    schemata = [line["schema"] for line in lines]
    assert schemata.count("Person") == 1
    assert schemata.count("Company") == 1
    assert schemata.count("Ownership") == 1
    # supplies has no FtM mapping in the bounded profile; confidential excluded.
    assert result["relationships"]["excluded_unmapped"] == 1
    assert result["entities"]["excluded_by_sensitivity"] == 1
    assert "Confidential Co" not in out.read_text(encoding="utf-8")
    ownership = [line for line in lines if line["schema"] == "Ownership"][0]
    assert ownership["properties"]["owner"] == ["ftm-person-001"]
    assert ownership["properties"]["percentage"] == ["40"]
    assert ownership["properties"]["startDate"] == ["2022-01-01"]
    assert ownership["properties"]["endDate"] == ["2024-06-30"]
    assert ownership["properties"]["role"] == ["beneficial owner"]
    # Deterministic: identical second export.
    again = tmp_path / "export2.ndjson"
    ftm_export(vault, again, sensitivity="internal")
    assert again.read_bytes() == out.read_bytes()


def test_limits_fail_closed(vault: Path, tmp_path: Path) -> None:
    profile = load_ftm_profile()
    profile["limits"]["max_entities"] = 2
    with pytest.raises(FtmAdapterError):
        parse_ftm_ndjson(FIXTURE, profile=profile)
    big = tmp_path / "big.ndjson"
    big.write_bytes(b"{}\n" * 1024)
    profile = load_ftm_profile()
    profile["limits"]["max_bytes"] = 100
    with pytest.raises(FtmAdapterError):
        parse_ftm_ndjson(big, profile=profile)


def test_cli_exchange_round_trip(vault: Path, tmp_path: Path) -> None:
    from constellation.cli import build_parser, run_action

    values = vars(build_parser().parse_args(
        ["exchange", str(vault), "ftm-import", str(FIXTURE), "--dry-run"]
    ))
    result = run_action(values.pop("command"), values)
    assert result["mode"] == "dry-run"
    assert result["entities"]["total"] == 4

    out = tmp_path / "cli-export.ndjson"
    values = vars(build_parser().parse_args(
        ["exchange", str(vault), "ftm-export", "--out", str(out)]
    ))
    result = run_action(values.pop("command"), values)
    assert result["status"] == "ok"
    assert out.is_file()
