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
        }
    ]


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
