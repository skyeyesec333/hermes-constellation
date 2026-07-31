"""Tests for the versioned relationship predicate registry (Wave 1 Task 1.1)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.models import RelationshipRecord, Sensitivity, generate_ulid
from constellation.predicates import (
    PredicateRegistryError,
    canonicalize_predicate,
    load_predicate_registry,
    predicate_stability,
    validate_relationship_semantics,
)

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

_HEADER = "version: 1\npredicates:\n"


def _write_registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(_HEADER + body, encoding="utf-8")
    return path


def _record(predicate: str, **overrides) -> RelationshipRecord:
    values = {
        "id": generate_ulid(),
        "title": "fixture",
        "status": "active",
        "sensitivity": Sensitivity.INTERNAL,
        "subject_id": generate_ulid(),
        "predicate": predicate,
        "object_id": generate_ulid(),
        "source_ids": [generate_ulid()],
        "evidence_class": "user-asserted",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return RelationshipRecord(**values)


def test_default_registry_loads_seed_vocabulary() -> None:
    registry = load_predicate_registry()
    names = {entry.name for entry in registry.entries}
    for seed in (
        "owns", "controls", "employed_by", "directs", "member_of", "advises",
        "funds", "partners_with", "competes_with", "supplies", "located_at",
        "reports_to", "associated_with", "served_with",
    ):
        assert seed in names


def test_duplicate_alias_rejected(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path,
        """
- name: owns
  inverse: owned_by
  directed: true
  symmetric: false
  domains: [person]
  ranges: [company]
  stability: durable
  aliases: [holder_of]
- name: controls
  inverse: controlled_by
  directed: true
  symmetric: false
  domains: [person]
  ranges: [company]
  stability: durable
  aliases: [holder_of]
""",
    )
    with pytest.raises(PredicateRegistryError):
        load_predicate_registry(path)


def test_missing_inverse_rejected(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path,
        """
- name: owns
  inverse: nonexistent_predicate
  directed: true
  symmetric: false
  domains: [person]
  ranges: [company]
  stability: durable
""",
    )
    with pytest.raises(PredicateRegistryError):
        load_predicate_registry(path)


def test_symmetric_predicate_cannot_have_different_inverse(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path,
        """
- name: partners_with
  inverse: owned_by
  directed: false
  symmetric: true
  domains: [company]
  ranges: [company]
  stability: standard
- name: owns
  inverse: owned_by
  directed: true
  symmetric: false
  domains: [person]
  ranges: [company]
  stability: durable
- name: owned_by
  inverse: owns
  directed: true
  symmetric: false
  domains: [company]
  ranges: [person]
  stability: durable
""",
    )
    with pytest.raises(PredicateRegistryError):
        load_predicate_registry(path)


def test_unknown_entity_kind_rejected(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path,
        """
- name: owns
  inverse: null
  directed: true
  symmetric: false
  domains: [galactic_empire]
  ranges: [company]
  stability: durable
""",
    )
    with pytest.raises(PredicateRegistryError):
        load_predicate_registry(path)


def test_alias_chain_rejected(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path,
        """
- name: owns
  inverse: null
  directed: true
  symmetric: false
  domains: [person]
  ranges: [company]
  stability: durable
  aliases: [owner_of]
- name: owner_of
  inverse: null
  directed: true
  symmetric: false
  domains: [person]
  ranges: [company]
  stability: durable
""",
    )
    with pytest.raises(PredicateRegistryError):
        load_predicate_registry(path)


def test_alias_canonicalization() -> None:
    registry = load_predicate_registry()
    resolution = canonicalize_predicate("works_at", registry)
    assert resolution.status == "alias"
    assert resolution.canonical == "employed_by"
    canonical = canonicalize_predicate("employed_by", registry)
    assert canonical.status == "canonical"
    assert canonical.canonical == "employed_by"


def test_deprecated_predicate_resolves() -> None:
    registry = load_predicate_registry()
    resolution = canonicalize_predicate("former_role", registry)
    assert resolution.status == "deprecated"
    assert resolution.canonical == "employed_by"


def test_unknown_predicate_is_advisory_warning() -> None:
    registry = load_predicate_registry()
    findings = validate_relationship_semantics(_record("totally_made_up"), registry)
    assert findings
    assert all(f.severity == "warning" for f in findings)
    assert any(f.code == "predicate_unknown" for f in findings)


def test_domain_range_warnings() -> None:
    registry = load_predicate_registry()
    findings = validate_relationship_semantics(
        _record("directs"), registry, subject_kind="company", object_kind="person"
    )
    codes = {f.code for f in findings}
    assert "predicate_domain" in codes
    assert "predicate_range" in codes
    assert all(f.severity == "warning" for f in findings)


def test_canonical_predicate_with_matching_kinds_is_clean() -> None:
    registry = load_predicate_registry()
    findings = validate_relationship_semantics(
        _record("owns"), registry, subject_kind="person", object_kind="company"
    )
    assert findings == []


def test_alias_use_warns_but_deprecated_also_warns() -> None:
    registry = load_predicate_registry()
    alias_findings = validate_relationship_semantics(_record("works_at"), registry)
    assert any(f.code == "predicate_alias" for f in alias_findings)
    deprecated_findings = validate_relationship_semantics(_record("former_role"), registry)
    assert any(f.code == "predicate_deprecated" for f in deprecated_findings)


def test_predicate_stability_shared_lookup() -> None:
    # Registry-known predicates resolve through the registry (aliases included).
    assert predicate_stability("owns") == "durable"
    assert predicate_stability("works_at") == "standard"
    # Legacy claim vocabulary preserved exactly.
    assert predicate_stability("founded_in") == "durable"
    assert predicate_stability("pricing") == "transient"
    # Unknown predicates default to standard.
    assert predicate_stability("never_seen_before") == "standard"


def test_confidence_uses_shared_stability_lookup() -> None:
    from datetime import timedelta

    from constellation.confidence import compute_confidence

    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    metadata = {
        "claim_status": "source-claimed",
        "predicate": "pricing",
        "source_ids": [generate_ulid()],
        "created_at": (now - timedelta(days=90)).isoformat(),
    }
    result = compute_confidence(metadata, now=now)
    assert result["stability"] == "transient"
    assert result["half_life_days"] == 14.0
