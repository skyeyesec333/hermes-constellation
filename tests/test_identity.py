import json
from datetime import UTC, datetime

from constellation.cli import main
from constellation.frontmatter import render_frontmatter
from constellation.identity import propose_identity_candidates
from constellation.models import EntityKind, EntityRecord, Sensitivity


def invoke(capsys, *args):
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)["result"]


def entity(identifier: str, title: str) -> EntityRecord:
    return EntityRecord(
        id=identifier,
        type=EntityKind.COMPANY,
        title=title,
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_identity_proposal_exposes_the_name_evidence_without_merging_entities():
    left = entity("01ARZ3NDEKTSV4RRFFQ69G5FAV", "Fictional Cobalt Logistics")
    right = entity("01ARZ3NDEKTSV4RRFFQ69G5FAW", "Fictional Cobalt Logistics Co., Ltd.")

    candidates = propose_identity_candidates([left, right])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.left_entity_id == left.id
    assert candidate.right_entity_id == right.id
    assert candidate.entity_kind == EntityKind.COMPANY
    assert candidate.status == "pending"
    assert candidate.score == 1.0
    assert candidate.factors[0].field == "name"
    assert candidate.factors[0].score == 1.0
    assert left.resolution_state == right.resolution_state == "unresolved"


def test_identity_candidates_are_deterministic_for_unchanged_entities():
    left = entity("01ARZ3NDEKTSV4RRFFQ69G5FAV", "Fictional Cobalt Logistics")
    right = entity("01ARZ3NDEKTSV4RRFFQ69G5FAW", "Fictional Cobalt Logistics Co., Ltd.")

    first = propose_identity_candidates([left, right])
    second = propose_identity_candidates([right, left])

    assert first == second


def test_resolve_propose_reads_canonical_entities_without_writing_or_merging(tmp_path, capsys):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))
    left = entity("01ARZ3NDEKTSV4RRFFQ69G5FAV", "Fictional Cobalt Logistics")
    right = entity("01ARZ3NDEKTSV4RRFFQ69G5FAW", "Fictional Cobalt Logistics Co., Ltd.")
    for record in (left, right):
        path = vault / "entities" / f"{record.id}.md"
        path.write_text(render_frontmatter(record.model_dump(mode="json"), "Fictional entity.\n"))

    result = invoke(capsys, "resolve", str(vault), "propose")

    assert result["status"] == "candidates_found"
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["left_entity_id"] == left.id
    assert not list((vault / ".constellation/candidates").iterdir())
    assert left.resolution_state == right.resolution_state == "unresolved"
