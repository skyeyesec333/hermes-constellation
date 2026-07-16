import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from constellation.models import (
    BaseRecord,
    CandidatePatch,
    Claim,
    ClaimStatus,
    EntityKind,
    EntityRecord,
    EntityResolutionState,
    ResearchRun,
    ResearchTerminalState,
    Sensitivity,
    SourceItem,
)
from constellation.schema import generate_artifacts, json_schema_text, note_template


def common(**overrides):
    values = {
        "type": "record",
        "title": "Fictional record",
        "status": "active",
        "sensitivity": Sensitivity.INTERNAL,
        "created_at": datetime(2026, 1, 2, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
    }
    values.update(overrides)
    return values


def test_base_record_requires_common_fields_and_rejects_unknown_values():
    with pytest.raises(ValidationError):
        BaseRecord()
    with pytest.raises(ValidationError):
        BaseRecord(**common(sensitivity="secret"))
    with pytest.raises(ValidationError):
        BaseRecord(**common(extra_field=True))


def test_generated_ids_are_ulid_form_and_immutable():
    record = BaseRecord(**common())
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", record.id)
    with pytest.raises(ValidationError):
        record.id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def test_specialized_records_validate_their_required_fields():
    source = SourceItem(
        **common(),
        source_hash="a" * 64,
        original_path="Library/Files/2026/source/file.txt",
        media_type="text/plain",
    )
    claim = Claim(
        **common(type="claim"),
        subject_id=source.id,
        predicate="works_at",
        object_literal="Fictional Corp",
        source_ids=[source.id],
    )
    candidate = CandidatePatch(
        **common(), target_path="claims/example.md", content="---\nid: example\n---\n"
    )
    run = ResearchRun(**common(status=ResearchTerminalState.COMPLETED))
    assert source.schema_version == claim.schema_version == candidate.schema_version == "0.1"
    assert run.can_promote is True
    with pytest.raises(ValidationError):
        SourceItem(**common(), source_hash="bad", original_path="x", media_type="text/plain")


def test_entity_records_have_controlled_identity_and_merge_fields():
    entity = EntityRecord(**common(type=EntityKind.PERSON), aliases=["Fictional Alias"])
    assert entity.resolution_state == "unresolved"
    assert entity.aliases == ["Fictional Alias"]
    with pytest.raises(ValidationError):
        EntityRecord.model_validate(common(type="unknown-kind"), strict=False)
    with pytest.raises(ValidationError, match="verified entities require evidence"):
        EntityRecord(
            **common(type=EntityKind.COMPANY),
            resolution_state=EntityResolutionState.VERIFIED,
        )
    with pytest.raises(ValidationError, match="merged entities require merged_into"):
        EntityRecord(
            **common(type=EntityKind.COMPANY),
            resolution_state=EntityResolutionState.MERGED,
        )


def test_partial_research_runs_cannot_promote():
    run = ResearchRun(**common(status=ResearchTerminalState.BUDGET_EXHAUSTED))
    assert run.can_promote is False


def test_canonical_research_receipt_must_match_its_terminal_run():
    run_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    receipt = {
        "version": 2,
        "run_id": run_id,
        "status": "partial",
        "promotion_allowed": False,
        "finished_at": "2026-01-02T00:00:00+00:00",
    }
    run = ResearchRun(
        **common(status=ResearchTerminalState.PARTIAL),
        id=run_id,
        receipt=receipt,
    )
    assert run.receipt["run_id"] == run.id
    with pytest.raises(ValidationError, match="status does not match"):
        ResearchRun(
            **common(status=ResearchTerminalState.COMPLETED),
            id=run_id,
            receipt=receipt,
        )


def test_claim_requires_object_id_or_object_literal():

    source_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    with pytest.raises(ValidationError, match="claim requires object_id or object_literal"):
        Claim(
            **common(type="claim"),
            subject_id=source_id,
            predicate="works_at",
            source_ids=[source_id],
        )
    with pytest.raises(ValidationError, match="claim cannot have both object_id and object_literal"):
        Claim(
            **common(type="claim"),
            subject_id=source_id,
            predicate="works_at",
            object_id=source_id,
            object_literal="Fictional Corp",
            source_ids=[source_id],
        )


def test_claim_supports_temporal_claims_and_cross_linking():
    source_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    other_id = "01ARZ3NDEKTSV4RRFFQ69G5FZZ"
    claim = Claim(
        **common(type="claim"),
        subject_id=source_id,
        predicate="worked_at",
        object_literal="Fictional Corp",
        source_ids=[source_id],
        claim_status=ClaimStatus.SUPERSEDED,
        observed_at=datetime(2025, 6, 1, tzinfo=UTC),
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        valid_to=datetime(2024, 12, 31, tzinfo=UTC),
        confidence=0.95,
        evidence_anchor="source-items/source-FZ.md#L10-L20",
        evidence_excerpt="Previously served as CTO at Fictional Corp until Dec 2024",
        contradicts=[other_id],
    )
    assert claim.claim_status == ClaimStatus.SUPERSEDED
    assert claim.valid_to is not None
    assert claim.confidence == 0.95
    assert claim.contradicts == [other_id]
    assert claim.evidence_anchor is not None

    with pytest.raises(ValidationError, match="valid_to cannot be earlier"):
        Claim(
            **common(type="claim"),
            subject_id=source_id,
            predicate="works_at",
            object_literal="Fictional Corp",
            source_ids=[source_id],
            valid_from=datetime(2025, 1, 1, tzinfo=UTC),
            valid_to=datetime(2024, 1, 1, tzinfo=UTC),
        )


def test_claim_defaults_to_source_claimed():

    source_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    claim = Claim(
        **common(type="claim"),
        subject_id=source_id,
        predicate="located_in",
        object_literal="Bangkok",
        source_ids=[source_id],
    )
    assert claim.claim_status == ClaimStatus.SOURCE_CLAIMED
    assert claim.contradicts == []
    assert claim.supports == []
    assert claim.supersedes == []


def test_schema_and_note_template_generation_are_deterministic():
    first = json_schema_text(SourceItem)
    second = json_schema_text(SourceItem)
    assert first == second
    assert json.loads(first)["title"] == "SourceItem"
    template = note_template(Claim)
    assert template == note_template(Claim)
    assert template.startswith("---\nschema_version: '0.1'\n")
    assert "type:" in template
    assert "status:" in template
    assert "updated_at:" in template
    assert "sensitivity:" in template


def test_committed_generated_artifacts_are_current(tmp_path):
    schema_dir = tmp_path / "schemas"
    template_dir = tmp_path / "templates"
    generated = generate_artifacts(schema_dir, template_dir)
    assert generated
    for path in generated:
        committed_root = (
            Path("resources/generated-json-schemas")
            if path.parent == schema_dir
            else Path("resources/generated-note-templates")
        )
        assert (committed_root / path.name).read_bytes() == path.read_bytes()
