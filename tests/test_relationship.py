from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from constellation.frontmatter import render_frontmatter
from constellation.models import RelationshipRecord, Sensitivity
from constellation.validation import validate_canonical_text
from constellation.vault import initialize_vault


SUBJECT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
OBJECT = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
SOURCE = "01ARZ3NDEKTSV4RRFFQ69G5FAX"


def relationship(**overrides):
    values = {
        "type": "relationship",
        "title": "Fictional company advises fictional project",
        "status": "active",
        "sensitivity": Sensitivity.INTERNAL,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
        "subject_id": SUBJECT,
        "predicate": "advises",
        "object_id": OBJECT,
        "source_ids": [SOURCE],
        "evidence_class": "single-source",
    }
    values.update(overrides)
    return values


def test_relationship_record_requires_sourced_distinct_endpoints():
    record = RelationshipRecord(**relationship())

    assert record.subject_id == SUBJECT
    assert record.object_id == OBJECT
    with pytest.raises(ValidationError, match="cannot relate to itself"):
        RelationshipRecord(**relationship(object_id=SUBJECT))
    with pytest.raises(ValidationError, match="source_ids"):
        RelationshipRecord(**relationship(source_ids=[]))


def test_relationship_records_are_canonical_and_validated(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    text = render_frontmatter(RelationshipRecord(**relationship()).model_dump(mode="json"), "Evidence.\n")

    assert (vault / "relationships").is_dir()
    validated = validate_canonical_text(text, "relationships/example.md")

    assert isinstance(validated, RelationshipRecord)
