"""Tests for temporal and bounded qualifier semantics on RelationshipRecord
(i2-successor Wave 1 Task 1.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from constellation.models import RelationshipRecord, Sensitivity, generate_ulid

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _record(**overrides) -> RelationshipRecord:
    values = {
        "id": generate_ulid(),
        "title": "fixture",
        "status": "active",
        "sensitivity": Sensitivity.INTERNAL,
        "subject_id": generate_ulid(),
        "predicate": "owns",
        "object_id": generate_ulid(),
        "source_ids": [generate_ulid()],
        "evidence_class": "user-asserted",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return RelationshipRecord(**values)


def test_legacy_relationship_remains_valid() -> None:
    record = _record()
    assert record.observed_at is None
    assert record.first_seen is None
    assert record.last_seen is None
    assert record.valid_from is None
    assert record.valid_to is None
    assert record.role == ""
    assert record.qualifiers == {}
    assert record.supersedes == []


def test_valid_interval_accepted() -> None:
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 6, 30, tzinfo=timezone.utc)
    record = _record(valid_from=start, valid_to=end, first_seen=start, last_seen=NOW)
    assert record.valid_from == start
    assert record.valid_to == end


def test_reversed_validity_interval_rejected() -> None:
    with pytest.raises(ValidationError, match="valid_to cannot be earlier"):
        _record(valid_from=NOW, valid_to=NOW - timedelta(days=1))


def test_reversed_observation_interval_rejected() -> None:
    with pytest.raises(ValidationError, match="last_seen cannot be earlier"):
        _record(first_seen=NOW, last_seen=NOW - timedelta(days=1))


def test_naive_temporal_fields_rejected() -> None:
    naive = datetime(2023, 1, 1, 0, 0)
    for field in ("observed_at", "first_seen", "last_seen", "valid_from", "valid_to"):
        with pytest.raises(ValidationError):
            _record(**{field: naive})


def test_timezone_aware_serialization_round_trip() -> None:
    bangkok = timezone(timedelta(hours=7))
    seen = datetime(2026, 1, 15, 9, 30, tzinfo=bangkok)
    record = _record(observed_at=seen, valid_from=seen)
    payload = record.model_dump(mode="json")
    assert "+07:00" in payload["observed_at"]
    assert datetime.fromisoformat(payload["observed_at"]) == seen
    assert datetime.fromisoformat(payload["valid_from"]) == seen


def test_qualifier_key_syntax_enforced() -> None:
    with pytest.raises(ValidationError):
        _record(qualifiers={"Bad Key": "x"})
    with pytest.raises(ValidationError):
        _record(qualifiers={"9starts_with_digit": "x"})
    with pytest.raises(ValidationError):
        _record(qualifiers={"a" * 65: "x"})
    record = _record(qualifiers={"percentage": "40", "ownership_type": "direct"})
    assert record.qualifiers["percentage"] == "40"


def test_qualifier_count_bounded() -> None:
    qualifiers = {f"q{i:02d}": "x" for i in range(21)}
    with pytest.raises(ValidationError):
        _record(qualifiers=qualifiers)
    record = _record(qualifiers={f"q{i:02d}": "x" for i in range(20)})
    assert len(record.qualifiers) == 20


def test_role_and_supersedes() -> None:
    prior = generate_ulid()
    record = _record(role="beneficial owner", supersedes=[prior])
    assert record.role == "beneficial owner"
    assert record.supersedes == [prior]


def test_model_input_is_not_mutated() -> None:
    qualifiers = {"percentage": "40"}
    supersedes = [generate_ulid()]
    sources = [generate_ulid()]
    record = _record(qualifiers=qualifiers, supersedes=supersedes, source_ids=sources)
    record.qualifiers["added_later"] = "y"
    record.supersedes.append(generate_ulid())
    record.source_ids.append(generate_ulid())
    assert qualifiers == {"percentage": "40"}
    assert len(supersedes) == 1
    assert len(sources) == 1
