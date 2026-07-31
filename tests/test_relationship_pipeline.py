"""Tests for the review-gated relationship pipeline (i2-successor Wave 1 Task 1.3)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.frontmatter import parse_frontmatter, render_frontmatter
from constellation.graph_surface import build_graph_projection
from constellation.models import (
    EntityKind,
    EntityRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.relationship import (
    RelationshipPipelineError,
    assertion_fingerprint,
    list_staged_relationships,
    stage_relationship,
    supersede_relationship,
)
from constellation.review import PromotionError, list_candidates, promote_candidate
from constellation.validation import validate_vault
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _write(vault: Path, folder: str, record, body: str = "# note\n") -> None:
    target = vault / folder / f"{record.id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_frontmatter(record.model_dump(mode="json", exclude_none=True), body),
        encoding="utf-8",
    )


def _entity(vault: Path, title: str, kind: EntityKind = EntityKind.COMPANY) -> str:
    record = EntityRecord(
        id=generate_ulid(), type=kind, title=title, status="active",
        sensitivity=Sensitivity.INTERNAL, source_ids=[], created_at=NOW, updated_at=NOW,
    )
    _write(vault, "entities" if kind != EntityKind.PERSON else "people", record)
    return record.id


def _source(vault: Path) -> str:
    record = SourceItem(
        id=generate_ulid(), type="source_item", title="Fictional filing",
        status="active", sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"bytes").hexdigest(),
        original_path="Library/Files/filing.pdf", media_type="application/pdf",
        created_at=NOW, updated_at=NOW,
    )
    _write(vault, "source-items", record)
    return record.id


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_vault(root)
    return root


def test_stage_relationship_writes_envelope_candidate(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)

    result = stage_relationship(
        vault, subject_id=subject, predicate="owns", object_id=obj,
        source_ids=[source], confidence=0.8, role="beneficial owner",
        qualifiers={"percentage": "40"},
        valid_from=datetime(2022, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2024, 6, 30, tzinfo=timezone.utc),
        evidence_excerpt="owns 40% of Fictional Holdings",
        evidence_anchor="page 3",
    )

    assert result["status"] == "staged"
    packet_path = vault / result["candidate_path"]
    assert packet_path.name.startswith("relationship-")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["kind"] == "relationship_candidate"
    assert packet["record"]["predicate"] == "owns"
    assert packet["record"]["qualifiers"] == {"percentage": "40"}
    assert packet["assertion_fingerprint"]
    assert packet["revision"]["number"] == 1
    # No canonical write happened.
    assert list((vault / "relationships").glob("*.md")) == []


def test_staging_is_idempotent_per_assertion_fingerprint(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)
    kwargs = dict(subject_id=subject, predicate="owns", object_id=obj, source_ids=[source])

    first = stage_relationship(vault, **kwargs)
    second = stage_relationship(vault, **kwargs)

    assert first["status"] == "staged"
    assert second["status"] == "already_staged"
    assert second["candidate_path"] == first["candidate_path"]
    candidates = list((vault / ".constellation/candidates").glob("relationship-*.json"))
    assert len(candidates) == 1


def test_unknown_predicate_fails_unless_experimental(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)

    with pytest.raises(RelationshipPipelineError):
        stage_relationship(
            vault, subject_id=subject, predicate="totally_invented",
            object_id=obj, source_ids=[source],
        )
    result = stage_relationship(
        vault, subject_id=subject, predicate="totally_invented",
        object_id=obj, source_ids=[source], experimental=True,
    )
    assert result["status"] == "staged"


def test_alias_predicate_stages_with_canonical_fingerprint(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)

    alias = stage_relationship(
        vault, subject_id=subject, predicate="works_at", object_id=obj, source_ids=[source]
    )
    assert alias["status"] == "staged"
    packet = json.loads((vault / alias["candidate_path"]).read_text(encoding="utf-8"))
    # Fingerprint uses the canonical predicate name.
    canonical_fp = assertion_fingerprint(
        subject_id=subject, predicate="employed_by", object_id=obj,
    )
    assert packet["assertion_fingerprint"] == canonical_fp


def test_staging_fails_closed_on_missing_references(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)

    with pytest.raises(RelationshipPipelineError):
        stage_relationship(
            vault, subject_id=generate_ulid(), predicate="owns", object_id=obj,
            source_ids=[source],
        )
    with pytest.raises(RelationshipPipelineError):
        stage_relationship(
            vault, subject_id=subject, predicate="owns", object_id=obj,
            source_ids=[generate_ulid()],
        )


def test_promote_relationship_candidate(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)
    staged = stage_relationship(
        vault, subject_id=subject, predicate="owns", object_id=obj,
        source_ids=[source], evidence_excerpt="owns 40%", evidence_anchor="page 3",
    )
    candidate_id = Path(staged["candidate_path"]).stem

    listed = list_candidates(vault)
    rel = [c for c in listed if c["kind"] == "relationship_candidate"]
    assert len(rel) == 1 and rel[0]["promotable"] is True

    result = promote_candidate(vault, candidate_id, confirm=True, expected_base_hash=None)
    assert result["status"] == "promoted"

    canonical = vault / result["target_path"]
    assert canonical.parent.name == "relationships"
    metadata, body = parse_frontmatter(canonical.read_text(encoding="utf-8"))
    assert metadata["predicate"] == "owns"
    assert metadata["subject_id"] == subject
    assert "owns 40%" in body
    assert "page 3" in body
    # Candidate consumed; vault validates clean.
    assert not (vault / ".constellation/candidates" / f"{candidate_id}.json").exists()
    report = validate_vault(vault)
    assert report["invalid"] == 0


def test_promotion_refuses_unknown_predicate_without_experimental(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)
    staged = stage_relationship(
        vault, subject_id=subject, predicate="totally_invented", object_id=obj,
        source_ids=[source], experimental=True,
    )
    packet_path = vault / staged["candidate_path"]
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["experimental"] = False
    packet_path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(PromotionError):
        promote_candidate(vault, packet_path.stem, confirm=True, expected_base_hash=None)


def test_promotion_refuses_missing_endpoint(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)
    staged = stage_relationship(
        vault, subject_id=subject, predicate="owns", object_id=obj, source_ids=[source]
    )
    # Remove the object entity after staging; promotion must fail closed.
    (vault / "entities" / f"{obj}.md").unlink()
    with pytest.raises(PromotionError):
        promote_candidate(
            vault, Path(staged["candidate_path"]).stem, confirm=True, expected_base_hash=None
        )


def test_list_staged_relationships_bounded(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)
    stage_relationship(vault, subject_id=subject, predicate="owns", object_id=obj,
                       source_ids=[source])

    listed = list_staged_relationships(vault)
    assert len(listed) == 1
    entry = listed[0]
    assert entry["predicate"] == "owns"
    assert entry["subject_id"] == subject
    assert entry["object_id"] == obj


def test_supersede_relationship_journaled_and_idempotent(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)
    old = stage_relationship(vault, subject_id=subject, predicate="owns", object_id=obj,
                             source_ids=[source], qualifiers={"percentage": "40"})
    old_id = promote_candidate(vault, Path(old["candidate_path"]).stem,
                               confirm=True, expected_base_hash=None)["target_path"]
    new = stage_relationship(vault, subject_id=subject, predicate="owns", object_id=obj,
                             source_ids=[source], qualifiers={"percentage": "55"})
    new_id = promote_candidate(vault, Path(new["candidate_path"]).stem,
                               confirm=True, expected_base_hash=None)["target_path"]
    old_ulid = Path(old_id).stem
    new_ulid = Path(new_id).stem

    result = supersede_relationship(
        vault, new_id=new_ulid, old_id=old_ulid, actor="test-reviewer",
        basis=[source],
    )
    assert result["status"] == "applied"
    old_meta, _ = parse_frontmatter((vault / old_id).read_text(encoding="utf-8"))
    new_meta, _ = parse_frontmatter((vault / new_id).read_text(encoding="utf-8"))
    assert old_meta["status"] == "superseded"
    assert old_ulid in new_meta["supersedes"]
    # Idempotent rerun.
    again = supersede_relationship(
        vault, new_id=new_ulid, old_id=old_ulid, actor="test-reviewer", basis=[source]
    )
    assert again["status"] == "already_applied"
    # Vault still validates.
    assert validate_vault(vault)["invalid"] == 0
    # Ledger recorded one application.
    ledger = (vault / ".constellation/supersedes-ledger.jsonl").read_text(encoding="utf-8")
    assert "relationship_supersede" in ledger


def test_supersede_refuses_already_terminal_without_force(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)

    def _promoted(**kwargs) -> str:
        staged = stage_relationship(
            vault, subject_id=subject, predicate="owns", object_id=obj,
            source_ids=[source], **kwargs,
        )
        target = promote_candidate(
            vault, Path(staged["candidate_path"]).stem, confirm=True, expected_base_hash=None
        )["target_path"]
        return Path(target).stem

    old_ulid = _promoted(qualifiers={"percentage": "40"})
    new_ulid = _promoted(qualifiers={"percentage": "55"})
    third_ulid = _promoted(qualifiers={"percentage": "60"})
    supersede_relationship(vault, new_id=new_ulid, old_id=old_ulid,
                           actor="test-reviewer", basis=[source])

    # Old is already terminal and NOT linked to the third record: refuse
    # without force; force stages a review candidate, never a direct write.
    with pytest.raises(RelationshipPipelineError):
        supersede_relationship(vault, new_id=third_ulid, old_id=old_ulid,
                               actor="test-reviewer", basis=[source])
    forced = supersede_relationship(vault, new_id=third_ulid, old_id=old_ulid,
                                    actor="test-reviewer", basis=[source], force=True)
    assert forced["status"] == "staged_review"
    third_meta, _ = parse_frontmatter(
        (vault / "relationships" / f"{third_ulid}.md").read_text(encoding="utf-8")
    )
    assert old_ulid not in (third_meta.get("supersedes") or [])


def test_projection_flags_relationship_candidate(vault: Path) -> None:
    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)
    stage_relationship(vault, subject_id=subject, predicate="advises", object_id=obj,
                       source_ids=[source])

    projection = build_graph_projection(vault)
    candidate_edges = [
        e for e in projection["edges"]
        if e["edge_source"] == "candidate_relationship" and e["candidate"] is True
    ]
    assert len(candidate_edges) == 1
    assert candidate_edges[0]["predicate"] == "advises"


def test_cli_stage_and_list_round_trip(vault: Path) -> None:
    from constellation.cli import build_parser, run_action

    subject = _entity(vault, "Fictional Person", EntityKind.PERSON)
    obj = _entity(vault, "Fictional Holdings")
    source = _source(vault)

    values = vars(build_parser().parse_args([
        "relationship", str(vault), "stage",
        "--subject-id", subject, "--predicate", "owns", "--object-id", obj,
        "--source-ids", source, "--qualifier", "percentage=40",
        "--valid-from", "2022-01-01T00:00:00+00:00",
        "--valid-to", "2024-06-30T00:00:00+00:00",
    ]))
    staged = run_action(values.pop("command"), values)
    assert staged["status"] == "staged"
    packet = json.loads((vault / staged["candidate_path"]).read_text(encoding="utf-8"))
    assert packet["record"]["qualifiers"] == {"percentage": "40"}
    assert packet["record"]["valid_from"].startswith("2022-01-01")

    values = vars(build_parser().parse_args(["relationship", str(vault), "list"]))
    listed = run_action(values.pop("command"), values)
    assert len(listed) == 1
    assert listed[0]["predicate"] == "owns"
