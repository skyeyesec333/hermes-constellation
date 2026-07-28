import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.cli import main
from constellation.crm import crm_apply, crm_plan
from constellation.frameworks import run_framework
from constellation.frontmatter import render_frontmatter
from constellation.identity import (
    SubjectResolutionError,
    normalize_identity_email,
    normalize_identity_phone,
    propose_identity_candidates,
    resolve_subject,
)
from constellation.models import EntityKind, EntityRecord, Sensitivity, generate_ulid
from constellation.prep import compile_prep
from constellation.vault import initialize_vault


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


def test_contact_normalizers_require_explicit_phone_region_and_preserve_email_canonical_form():
    assert normalize_identity_email("Fictional.User@" + "EXAMPLE." + "COM") == (
        "fictional.user@" + "example." + "com"
    )
    assert normalize_identity_phone("(415) 555-2671", region="US") == "+1" + "415" + "555" + "2671"
    assert normalize_identity_phone("415 555 2671", region=None) is None


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


RESOLUTION_NOW = datetime(2026, 7, 28, 3, 30, tzinfo=UTC)


def _resolution_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    (vault / "people").mkdir(exist_ok=True)
    return vault


def _write_subject(
    vault: Path,
    folder: str,
    filename: str,
    kind: EntityKind,
    *,
    subject_id: str | None = None,
) -> EntityRecord:
    subject = EntityRecord(
        id=subject_id or generate_ulid(),
        type=kind,
        title="Ada Example" if kind is EntityKind.PERSON else "Example Labs",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=RESOLUTION_NOW,
        updated_at=RESOLUTION_NOW,
    )
    (vault / folder / filename).write_text(
        render_frontmatter(
            subject.model_dump(mode="json", exclude_none=True),
            f"# {subject.title}\n\nCanonical context.\n",
        ),
        encoding="utf-8",
    )
    return subject


def test_resolves_slug_named_person_by_frontmatter_id(tmp_path: Path) -> None:
    vault = _resolution_vault(tmp_path)
    person = _write_subject(vault, "people", "person-ada-example.md", EntityKind.PERSON)

    resolved = resolve_subject(vault, person.id)

    assert resolved.path.relative_to(vault).as_posix() == "people/person-ada-example.md"
    assert resolved.record == person
    assert resolved.body == "# Ada Example\n\nCanonical context.\n"


def test_prep_uses_resolver_for_slug_named_person(tmp_path: Path) -> None:
    vault = _resolution_vault(tmp_path)
    person = _write_subject(vault, "people", "person-ada-example.md", EntityKind.PERSON)

    brief = compile_prep(vault, person.id)

    assert "# Prep Brief: Ada Example" in brief
    assert "**Kind:** person" in brief


def test_framework_uses_resolver_for_slug_named_company(tmp_path: Path) -> None:
    vault = _resolution_vault(tmp_path)
    company = _write_subject(vault, "entities", "company-example-labs.md", EntityKind.COMPANY)

    result = run_framework(vault, company.id, "swot")

    assert result["status"] == "staged"
    assert result["entity_id"] == company.id


def test_crm_reads_and_updates_resolved_slug_path(tmp_path: Path) -> None:
    vault = _resolution_vault(tmp_path)
    company = _write_subject(vault, "entities", "company-example-labs.md", EntityKind.COMPANY)

    from constellation.models import Interaction, InteractionType

    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    interaction = Interaction(
        id=generate_ulid(),
        title="Intro call",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        interaction_type=InteractionType.MEETING,
        subject_ids=[company.id],
        participants=[company.id],
        channel="phone",
        summary="Discussed partnership",
        occurred_at=now,
        created_at=now,
        updated_at=now,
    )
    (vault / "interactions" / f"{interaction.id}.md").write_text(
        render_frontmatter(interaction.model_dump(mode="json", exclude_none=True), "# Interaction\n"),
        encoding="utf-8",
    )

    proposal = crm_plan(vault, entity_id=company.id)[0]
    changes = proposal["proposed"]
    assert isinstance(changes, dict)
    assert changes.get("stage") == "engaged"
    result = crm_apply(
        vault,
        company.id,
        expected_sha256=str(proposal["expected_sha256"]),
        changes=changes,
    )

    assert result["status"] == "applied"
    assert proposal["entity_path"] == "entities/company-example-labs.md"
    assert not (vault / "entities" / f"{company.id}.md").exists()


def test_rejects_subject_in_wrong_canonical_route(tmp_path: Path) -> None:
    vault = _resolution_vault(tmp_path)
    person = _write_subject(vault, "entities", "person-ada-example.md", EntityKind.PERSON)

    with pytest.raises(SubjectResolutionError, match="route does not match type"):
        resolve_subject(vault, person.id)


def test_rejects_duplicate_subject_id_across_routes(tmp_path: Path) -> None:
    vault = _resolution_vault(tmp_path)
    subject_id = generate_ulid()
    _write_subject(
        vault,
        "people",
        "person-ada-example.md",
        EntityKind.PERSON,
        subject_id=subject_id,
    )
    _write_subject(
        vault,
        "entities",
        "company-example-labs.md",
        EntityKind.COMPANY,
        subject_id=subject_id,
    )

    with pytest.raises(SubjectResolutionError, match="ambiguous"):
        resolve_subject(vault, subject_id)


def test_rejects_exact_filename_with_different_frontmatter_id(tmp_path: Path) -> None:
    vault = _resolution_vault(tmp_path)
    requested_id = generate_ulid()
    _write_subject(
        vault,
        "entities",
        f"{requested_id}.md",
        EntityKind.COMPANY,
    )

    with pytest.raises(SubjectResolutionError, match="filename does not match"):
        resolve_subject(vault, requested_id)


def test_rejects_symlink_subject_path(tmp_path: Path) -> None:
    vault = _resolution_vault(tmp_path)
    company = _write_subject(vault, "entities", "company-example-labs.md", EntityKind.COMPANY)
    link = vault / "entities" / f"{company.id}.md"
    link.symlink_to(vault / "entities" / "company-example-labs.md")

    with pytest.raises(SubjectResolutionError, match="symlink"):
        resolve_subject(vault, company.id)
