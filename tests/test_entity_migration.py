import constellation.entity_migration as entity_migration
from constellation.entity_migration import execute_entity_migration, plan_entity_migration
from constellation.frontmatter import parse_frontmatter
from constellation.models import EntityKind
from constellation.validation import validate_canonical_text
from constellation.vault import initialize_vault


def test_plan_entity_migration_discovers_records_in_declared_legacy_folders(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    (vault / "people").mkdir()
    (vault / "companies").mkdir()
    (vault / "people/ada-example.md").write_text(
        "---\ntitle: Ada Example\n---\n\n# Ada Example\n\nLegacy person context.\n",
        encoding="utf-8",
    )
    (vault / "companies/example-labs.md").write_text(
        "---\ntitle: Example Labs\n---\n\n# Example Labs\n\nLegacy company context.\n",
        encoding="utf-8",
    )

    plan = plan_entity_migration(vault)

    assert plan["totals"]["physical"] == 2
    assert plan["totals"]["new"] == 2
    assert plan["totals"]["reconciled"] is True
    assert {entry["legacy_path"] for entry in plan["new_entities"]} == {
        "people/ada-example.md",
        "companies/example-labs.md",
    }


def test_execute_entity_migration_writes_valid_canonical_markdown_with_preserved_body(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    preserved_body = "# Ada Example\n\nLegacy context must survive.\n"
    monkeypatch.setattr(
        entity_migration,
        "_LEGACY_FOLDERS",
        (("person", EntityKind.PERSON),),
    )
    (vault / "person").mkdir()
    (vault / "person/ada-example.md").write_text(
        "---\ntitle: Ada Example\n---\n" + preserved_body,
        encoding="utf-8",
    )

    result = execute_entity_migration(vault, dry_run=False)

    assert result["promoted"] == 1
    emitted = next((vault / "entities").glob("*.md"))
    emitted_text = emitted.read_text(encoding="utf-8")
    validate_canonical_text(emitted_text, emitted.relative_to(vault).as_posix())
    _, body = parse_frontmatter(emitted_text)
    assert preserved_body.strip() in body
