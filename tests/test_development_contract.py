import stat
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PUBLIC_CONTRACT = {
    ".github/workflows/ci.yml",
    ".kilo/agents/constellation-maintainer.md",
    ".kilo/agents/constellation-reviewer.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "ROADMAP.md",
    "SECURITY.md",
    "docs/plans/01-release-integrity.md",
    "scripts/verify_fast.sh",
    "scripts/verify_release.sh",
    "tests/test_development_contract.py",
}


def _frontmatter(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    _, raw, body = content.split("---\n", 2)
    assert body.strip()
    return yaml.safe_load(raw)


def test_cross_model_development_contract_is_discoverable_and_public():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    manifest = yaml.safe_load((ROOT / "resources/public-lineage.yaml").read_text(encoding="utf-8"))

    assert "ROADMAP.md" in agents
    assert "scripts/verify_fast.sh" in agents
    assert "scripts/verify_release.sh" in agents
    assert "docs/plans/01-release-integrity.md" in roadmap

    declared = set(manifest["files"])
    assert REQUIRED_PUBLIC_CONTRACT <= declared
    for relative in REQUIRED_PUBLIC_CONTRACT:
        assert (ROOT / relative).is_file(), relative


def test_kilo_agents_are_provider_neutral_and_do_not_spawn_children():
    maintainer = _frontmatter(ROOT / ".kilo/agents/constellation-maintainer.md")
    reviewer = _frontmatter(ROOT / ".kilo/agents/constellation-reviewer.md")

    assert maintainer["mode"] == "primary"
    assert maintainer["permission"]["task"] == "deny"
    assert "model" not in maintainer

    assert reviewer["mode"] == "primary"
    assert reviewer["permission"]["task"] == "deny"
    assert reviewer["permission"]["edit"] == "deny"
    assert "model" not in reviewer


def test_verification_entrypoints_are_executable():
    for relative in ("scripts/verify_fast.sh", "scripts/verify_release.sh"):
        mode = (ROOT / relative).stat().st_mode
        assert mode & stat.S_IXUSR, relative
