import json
from datetime import UTC, datetime

from constellation.cli import main
from constellation.frontmatter import render_frontmatter
from constellation.graph import neighbors, path
from constellation.models import RelationshipRecord, Sensitivity
from constellation.vault import initialize_vault


SUBJECT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
OBJECT = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
SOURCE = "01ARZ3NDEKTSV4RRFFQ69G5FAX"


def invoke(capsys, *args):
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)["result"]


def relationship() -> RelationshipRecord:
    return RelationshipRecord(
        id="01ARZ3NDEKTSV4RRFFQ69G5FAY",
        type="relationship",
        title="Fictional company advises fictional project",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        subject_id=SUBJECT,
        predicate="advises",
        object_id=OBJECT,
        source_ids=[SOURCE],
        evidence_class="single-source",
    )


def test_neighbors_returns_only_sourced_relationships_for_the_requested_entity(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    record = relationship()
    (vault / "relationships/example.md").write_text(
        render_frontmatter(record.model_dump(mode="json"), "Evidence.\n"), encoding="utf-8"
    )

    result = neighbors(vault, SUBJECT)

    assert result["status"] == "relationships_found"
    assert result["relationships"] == [
        {
            "relationship_id": record.id,
            "subject_id": SUBJECT,
            "predicate": "advises",
            "object_id": OBJECT,
            "source_ids": [SOURCE],
            "evidence_class": "single-source",
            "sensitivity": "internal",
            "confidence": None,
            "observed_at": None,
            "valid_from": None,
            "valid_to": None,
            "temporal_status": "unknown",
        }
    ]


def _write_relationship(vault, record) -> None:
    (vault / "relationships" / f"{record.id}.md").write_text(
        render_frontmatter(record.model_dump(mode="json"), "Evidence.\n"), encoding="utf-8"
    )


def test_neighbors_direction_filters(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _write_relationship(vault, relationship())

    outgoing = neighbors(vault, SUBJECT, direction="outgoing")
    assert outgoing["status"] == "relationships_found"
    incoming = neighbors(vault, SUBJECT, direction="incoming")
    assert incoming["status"] == "no_relationships_found"
    incoming_obj = neighbors(vault, OBJECT, direction="incoming")
    assert incoming_obj["status"] == "relationships_found"


def test_neighbors_predicates_and_confidence_filters(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _write_relationship(vault, relationship())

    assert neighbors(vault, SUBJECT, predicates={"owns"})["status"] == "no_relationships_found"
    assert neighbors(vault, SUBJECT, predicates={"advises"})["status"] == "relationships_found"
    # Confidence filter excludes records without confidence when set.
    assert neighbors(vault, SUBJECT, min_confidence=0.5)["status"] == "no_relationships_found"
    confident = relationship().model_copy(update={"id": "01ARZ3NDEKTSV4RRFFQ69G5FB1", "confidence": 0.9})
    _write_relationship(vault, confident)
    assert len(neighbors(vault, SUBJECT, min_confidence=0.5)["relationships"]) == 1


def test_neighbors_as_of_excludes_expired_and_marks_unknown(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    dated = relationship().model_copy(update={
        "valid_from": datetime(2022, 1, 1, tzinfo=UTC),
        "valid_to": datetime(2024, 6, 30, tzinfo=UTC),
    })
    _write_relationship(vault, dated)

    inside = neighbors(vault, SUBJECT, as_of=datetime(2023, 1, 1, tzinfo=UTC))
    assert inside["status"] == "relationships_found"
    assert inside["relationships"][0]["temporal_status"] == "active"
    after = neighbors(vault, SUBJECT, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert after["status"] == "no_relationships_found"
    assert after["excluded_by_as_of"] == 1

    undated = relationship().model_copy(update={"id": "01ARZ3NDEKTSV4RRFFQ69G5FB2"})
    _write_relationship(vault, undated)
    mixed = neighbors(vault, SUBJECT, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert len(mixed["relationships"]) == 1
    assert mixed["relationships"][0]["temporal_status"] == "unknown"


def test_path_direction_directed_blocks_reverse_traversal(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _write_relationship(vault, relationship())

    forward = path(vault, SUBJECT, OBJECT, direction="directed")
    assert forward["status"] == "path_found"
    reverse = path(vault, OBJECT, SUBJECT, direction="directed")
    assert reverse["status"] == "no_path_found"
    # Default behavior unchanged: undirected traversal still finds the reverse path.
    assert path(vault, OBJECT, SUBJECT)["status"] == "path_found"


def test_path_as_of_and_predicates_filters(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    dated = relationship().model_copy(update={
        "valid_from": datetime(2022, 1, 1, tzinfo=UTC),
        "valid_to": datetime(2024, 6, 30, tzinfo=UTC),
    })
    _write_relationship(vault, dated)

    assert path(vault, SUBJECT, OBJECT, as_of=datetime(2023, 1, 1, tzinfo=UTC))["status"] == "path_found"
    assert path(vault, SUBJECT, OBJECT, as_of=datetime(2025, 1, 1, tzinfo=UTC))["status"] == "no_path_found"
    assert path(vault, SUBJECT, OBJECT, predicates={"owns"})["status"] == "no_path_found"


def test_path_returns_a_bounded_evidence_backed_chain(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    third = "01ARZ3NDEKTSV4RRFFQ69G5FAZ"
    first = relationship()
    second = first.model_copy(
        update={
            "id": "01ARZ3NDEKTSV4RRFFQ69G5FB0",
            "subject_id": OBJECT,
            "object_id": third,
            "predicate": "introduced_by",
        }
    )
    for record in (first, second):
        (vault / "relationships" / f"{record.id}.md").write_text(
            render_frontmatter(record.model_dump(mode="json"), "Evidence.\n"), encoding="utf-8"
        )

    result = path(vault, SUBJECT, third, max_hops=2)

    assert result["status"] == "path_found"
    assert [edge["relationship_id"] for edge in result["path"]] == [first.id, second.id]


def test_graph_cli_exposes_bounded_neighbor_query(tmp_path, capsys):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))
    record = relationship()
    (vault / "relationships/example.md").write_text(
        render_frontmatter(record.model_dump(mode="json"), "Evidence.\n"), encoding="utf-8"
    )

    result = invoke(capsys, "graph", str(vault), "neighbors", "--entity", SUBJECT)

    assert result["status"] == "relationships_found"
    assert result["relationships"][0]["relationship_id"] == record.id
