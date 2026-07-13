from pathlib import Path

import yaml

from constellation.validation import validate_vault


def test_bundled_skill_has_valid_frontmatter_and_links():
    path = Path("skills/constellation/SKILL.md")
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    _, raw_frontmatter, _body = content.split("---\n", 2)
    frontmatter = yaml.safe_load(raw_frontmatter)
    assert frontmatter["name"] == "constellation"
    assert len(frontmatter["description"]) <= 1024
    assert (path.parent / "references/architecture.md").is_file()
    assert (path.parent / "references/token-aware-research.md").is_file()


def test_demo_vault_uses_only_reserved_domains():
    demo = Path("examples/synthetic-demo-vault")
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in demo.rglob("*")
        if path.is_file()
    )
    for token in text.split():
        if "@" in token or token.startswith("http"):
            assert "example.test" in token


def test_demo_vault_is_initialized_and_canonical_records_validate():
    demo = Path("examples/synthetic-demo-vault")
    report = validate_vault(demo)
    assert report["valid"] == 2
    assert report["invalid"] == 0
