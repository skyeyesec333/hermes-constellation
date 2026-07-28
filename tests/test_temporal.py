"""Tests for temporal/as-of retrieval."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.models import (
    Claim,
    Decision,
    EntityKind,
    EntityRecord,
    Event,
    Observation,
    Sensitivity,
    generate_ulid,
)
from constellation.temporal import entity_timeline
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _write(vault: Path, folder: str, record) -> str:
    (vault / folder / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {record.title}\n"),
        encoding="utf-8",
    )
    return record.id


def _vault_with_entity(tmp_path: Path) -> tuple[Path, str, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    entity = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.COMPANY,
        title="TemporalCo",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    source = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.COMPANY,
        title="OtherCo",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    _write(vault, "entities", entity)
    _write(vault, "entities", source)
    return vault, entity.id, source.id


def test_entity_timeline_returns_cited_ordered_records(tmp_path: Path) -> None:
    vault, entity_id, source_id = _vault_with_entity(tmp_path)
    claim = Claim(
        id=generate_ulid(), title="Early claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="competes_in", object_literal="logistics",
        source_ids=[source_id], created_at=datetime(2026, 1, 10, tzinfo=timezone.utc), updated_at=NOW,
    )
    event = Event(
        id=generate_ulid(), title="Launch event", status="active",
        sensitivity=Sensitivity.INTERNAL, entity_ids=[entity_id],
        event_date="2026-03-15", event_type="product_launch",
        description="Launched v2", created_at=NOW, updated_at=NOW,
    )
    decision = Decision(
        id=generate_ulid(), title="Partner decision", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        decision="Partner with TemporalCo", rationale="Strong roadmap",
        source_ids=[], created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), updated_at=NOW,
    )
    _write(vault, "claims", claim)
    _write(vault, "events", event)
    _write(vault, "decisions", decision)

    timeline = entity_timeline(vault, entity_id)

    assert [entry["type"] for entry in timeline["entries"]] == ["claim", "event", "decision"]
    for entry in timeline["entries"]:
        assert entry["path"].endswith(".md")
        assert entry["timestamp"]
    assert timeline["truncated_by_as_of"] is False


def test_entity_timeline_as_of_excludes_later_records_and_reports_truncation(tmp_path: Path) -> None:
    vault, entity_id, source_id = _vault_with_entity(tmp_path)
    early = Claim(
        id=generate_ulid(), title="Early claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="competes_in", object_literal="logistics",
        source_ids=[source_id], created_at=datetime(2026, 1, 10, tzinfo=timezone.utc), updated_at=NOW,
    )
    late = Claim(
        id=generate_ulid(), title="Late claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="entered_market", object_literal="shipping",
        source_ids=[source_id], created_at=datetime(2026, 6, 10, tzinfo=timezone.utc), updated_at=NOW,
    )
    _write(vault, "claims", early)
    _write(vault, "claims", late)

    timeline = entity_timeline(vault, entity_id, as_of="2026-03-01T00:00:00+00:00")

    assert len(timeline["entries"]) == 1
    assert timeline["entries"][0]["id"] == early.id
    assert timeline["truncated_by_as_of"] is True


def test_entity_timeline_enforces_sensitivity_ceiling(tmp_path: Path) -> None:
    vault, entity_id, source_id = _vault_with_entity(tmp_path)
    public_claim = Claim(
        id=generate_ulid(), title="Public claim", status="active",
        sensitivity=Sensitivity.PUBLIC, subject_id=entity_id,
        predicate="competes_in", object_literal="logistics",
        source_ids=[source_id], created_at=NOW, updated_at=NOW,
    )
    confidential_claim = Claim(
        id=generate_ulid(), title="Secret claim", status="active",
        sensitivity=Sensitivity.CONFIDENTIAL, subject_id=entity_id,
        predicate="secret_plan", object_literal="acquisition",
        source_ids=[source_id], created_at=NOW, updated_at=NOW,
    )
    _write(vault, "claims", public_claim)
    _write(vault, "claims", confidential_claim)

    timeline = entity_timeline(vault, entity_id, sensitivity_ceiling="public")

    assert {entry["id"] for entry in timeline["entries"]} == {public_claim.id}
    assert timeline["excluded_by_sensitivity"] == 1


def test_observation_event_link_visible_in_timeline(tmp_path: Path) -> None:
    vault, entity_id, _ = _vault_with_entity(tmp_path)
    watchlist_id = generate_ulid()
    snapshot_id = generate_ulid()
    observation = Observation(
        id=generate_ulid(), title="Material change", status="active",
        sensitivity=Sensitivity.INTERNAL, watchlist_id=watchlist_id,
        snapshot_id=snapshot_id, change_summary="Detected expansion",
        entity_ids=[entity_id], source_ids=[], created_at=datetime(2026, 4, 1, tzinfo=timezone.utc), updated_at=NOW,
    )
    event = Event(
        id=generate_ulid(), title="Expansion event", status="active",
        sensitivity=Sensitivity.INTERNAL, entity_ids=[entity_id],
        event_date="2026-04-02", event_type="expansion",
        description="Expansion confirmed", observation_ids=[observation.id],
        source_ids=[], created_at=NOW, updated_at=NOW,
    )
    _write(vault, "observations", observation)
    _write(vault, "events", event)

    timeline = entity_timeline(vault, entity_id)

    types = [entry["type"] for entry in timeline["entries"]]
    assert "observation" in types
    assert "event" in types
    event_entry = next(entry for entry in timeline["entries"] if entry["type"] == "event")
    assert observation.id in event_entry.get("observation_ids", [])


def test_timeline_cli_with_as_of_and_ceiling(tmp_path: Path) -> None:
    from constellation.cli import build_parser, run_action

    vault, entity_id, source_id = _vault_with_entity(tmp_path)
    claim = Claim(
        id=generate_ulid(), title="Timeline CLI claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="competes_in", object_literal="logistics",
        source_ids=[source_id], created_at=NOW, updated_at=NOW,
    )
    _write(vault, "claims", claim)

    values = vars(
        build_parser().parse_args(
            ["timeline", str(vault), entity_id, "--as-of", "2026-12-31T00:00:00+00:00"]
        )
    )
    result = run_action(str(values.pop("command")), values)

    assert result["total_entries"] == 1
    assert result["entries"][0]["id"] == claim.id


def test_timeline_rejects_naive_as_of(tmp_path: Path) -> None:
    vault, entity_id, _ = _vault_with_entity(tmp_path)
    with pytest.raises(Exception, match="timezone"):
        entity_timeline(vault, entity_id, as_of="2026-03-01T00:00:00")


def test_timeline_surface_renders_offline_cited_html(tmp_path: Path) -> None:
    from constellation.timeline_surface import render_timeline_surface

    vault, entity_id, source_id = _vault_with_entity(tmp_path)
    claim = Claim(
        id=generate_ulid(), title="Surface claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="competes_in", object_literal="logistics",
        source_ids=[source_id], created_at=NOW, updated_at=NOW,
    )
    _write(vault, "claims", claim)

    timeline = entity_timeline(vault, entity_id)
    html = render_timeline_surface(timeline, entity_title="TemporalCo")

    assert "http://" not in html and "https://" not in html
    assert "<script" not in html
    assert "TemporalCo" in html
    assert "Surface claim" in html
    assert "claims/" in html


def test_timeline_surface_marks_truncation_visibly(tmp_path: Path) -> None:
    from constellation.timeline_surface import render_timeline_surface

    vault, entity_id, source_id = _vault_with_entity(tmp_path)
    late = Claim(
        id=generate_ulid(), title="Late claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="entered_market", object_literal="shipping",
        source_ids=[source_id], created_at=NOW, updated_at=NOW,
    )
    _write(vault, "claims", late)

    timeline = entity_timeline(vault, entity_id, as_of="2026-01-01T00:00:00+00:00")
    html = render_timeline_surface(timeline, entity_title="TemporalCo")

    assert timeline["truncated_by_as_of"] is True
    assert "truncated" in html.lower()


def test_timeline_surface_cli_writes_file_with_confirmation(tmp_path: Path) -> None:
    from constellation.cli import build_parser, run_action

    vault, entity_id, source_id = _vault_with_entity(tmp_path)
    claim = Claim(
        id=generate_ulid(), title="CLI surface claim", status="active",
        sensitivity=Sensitivity.INTERNAL, subject_id=entity_id,
        predicate="competes_in", object_literal="logistics",
        source_ids=[source_id], created_at=NOW, updated_at=NOW,
    )
    _write(vault, "claims", claim)
    output = tmp_path / "timeline.html"

    values = vars(
        build_parser().parse_args(
            ["timeline-surface", str(vault), entity_id, "--output", str(output)]
        )
    )
    result = run_action(str(values.pop("command")), values)

    assert result["status"] == "written"
    assert result["bytes_written"] == output.stat().st_size
    assert "CLI surface claim" in output.read_text(encoding="utf-8")
