"""Tests for record-health linting."""

from datetime import datetime, timezone
from pathlib import Path

from constellation.frontmatter import render_frontmatter
from constellation.models import (
    Claim,
    EntityKind,
    EntityRecord,
    Sensitivity,
    generate_ulid,
)
from constellation.record_lint import lint_records
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _write(vault: Path, folder: str, record) -> None:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n"),
        encoding="utf-8",
    )


def _entity(vault: Path, title: str = "TestCo") -> str:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title,
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities", record)
    return record.id


def test_clean_vault_reports_no_findings(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _entity(vault)

    result = lint_records(vault)

    assert result["findings"] == []
    assert result["summary"]["total"] == 0


def _write_raw_claim(vault: Path, claim_id: str, subject_id: str, title: str,
                     source_ids: list[str], predicate: str = "competes_in",
                     obj: str = "logistics") -> None:
    """Write a claim bypassing model validation (legacy/drifted records exist
    in real vaults; the linter must catch what the schema now forbids)."""
    metadata = {
        "schema_version": "0.1", "id": claim_id, "type": "claim",
        "title": title, "status": "active", "sensitivity": "internal",
        "subject_id": subject_id, "predicate": predicate,
        "object_literal": obj, "source_ids": source_ids,
        "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
    }
    (vault / "claims" / f"{claim_id}.md").write_text(
        render_frontmatter(metadata, f"# {title}\n"), encoding="utf-8"
    )


def test_claim_without_source_anchors_flagged_high(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity_id = _entity(vault)
    claim_id = generate_ulid()
    _write_raw_claim(vault, claim_id, entity_id, "Anchorless", [])

    result = lint_records(vault)

    finding = next(f for f in result["findings"] if f["check"] == "claim_without_sources")
    assert finding["severity"] == "high"
    assert finding["record_id"] == claim_id
    assert "claims/" in finding["record_path"]
    assert finding["suggested_action"]


def test_broken_source_reference_flagged(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity_id = _entity(vault)
    claim = Claim(
        id=generate_ulid(), title="Broken anchor", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="competes_in", object_literal="logistics",
        source_ids=[generate_ulid()],  # does not exist
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "claims", claim)

    result = lint_records(vault)

    assert any(f["check"] == "broken_source_reference" and f["severity"] == "high"
               for f in result["findings"])


def test_contradictory_active_claims_flagged(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity_id = _entity(vault)
    source_id = generate_ulid()
    for value in ("logistics", "fintech"):
        _write_raw_claim(vault, generate_ulid(), entity_id,
                         f"Competes in {value}", [source_id],
                         predicate="primary_market", obj=value)

    result = lint_records(vault)

    contradictions = [f for f in result["findings"] if f["check"] == "contradictory_claims"]
    assert contradictions
    assert contradictions[0]["severity"] == "medium"
    assert "primary_market" in contradictions[0]["detail"]


def test_lint_never_mutates_records(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity_id = _entity(vault)
    _write_raw_claim(vault, generate_ulid(), entity_id, "Anchorless", [])
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}

    lint_records(vault)

    after = {p: p.read_bytes() for p in vault.rglob("*.md")}
    assert before == after
