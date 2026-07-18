"""Tests for strategic framework execution."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frameworks import FrameworkError, run_framework
from constellation.frontmatter import render_frontmatter
from constellation.models import EntityKind, EntityRecord, Sensitivity, generate_ulid
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


def _setup_vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    entity = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.ORGANIZATION,
        title="TestCo",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "entities" / f"{entity.id}.md").write_text(
        render_frontmatter(entity.model_dump(mode="json", exclude_none=True), "# TestCo\n"),
        encoding="utf-8",
    )
    return vault, entity.id


def test_porter_stages_analysis_candidate(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)

    result = run_framework(vault, entity_id, "porter_five_forces")
    assert result["status"] == "staged"
    assert result["framework"] == "porter_five_forces"
    assert result["entity_id"] == entity_id
    assert "analysis_id" in result
    assert "candidate_path" in result

    # Verify candidate file exists
    candidate = vault / result["candidate_path"]
    assert candidate.is_file()


def test_swot_stages_analysis_candidate(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)

    result = run_framework(vault, entity_id, "swot")
    assert result["status"] == "staged"
    assert result["framework"] == "swot"


def test_unknown_framework_rejected(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    with pytest.raises(FrameworkError, match="unsupported framework"):
        run_framework(vault, entity_id, "pestle")


def test_missing_entity_rejected(tmp_path: Path) -> None:
    vault, _ = _setup_vault(tmp_path)
    with pytest.raises(FrameworkError, match="entity not found"):
        run_framework(vault, "nonexistent", "porter_five_forces")


def test_analysis_candidate_is_review_required(tmp_path: Path) -> None:
    vault, entity_id = _setup_vault(tmp_path)
    result = run_framework(vault, entity_id, "porter_five_forces")

    import json
    candidate_data = json.loads((vault / result["candidate_path"]).read_text())
    assert candidate_data["operator_reviewed"] is False


def test_analyze_cli_args() -> None:
    from constellation.cli import build_parser

    values = vars(
        build_parser().parse_args([
            "analyze", "/tmp/vault", "porter",
            "--entity-id", "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        ])
    )
    assert values["framework"] == "porter"
    assert values["entity_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
