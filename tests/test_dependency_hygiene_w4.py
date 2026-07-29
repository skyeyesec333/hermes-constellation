"""Wave 4 dependency hygiene guard.

Locks in the offline-surface contract:
- core (non-optional) dependencies stay minimal — heavy libraries live in
  optional extras only;
- rendering/projection modules (graph_surface, graph_api, briefing) import
  only stdlib and constellation — no third-party or network libraries, so
  offline surfaces can never gain a runtime dependency or leak egress.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# stdlib allowlist is derived from the running interpreter
_STDLIB = set(sys.stdlib_module_names)
_OFFLINE_MODULES = ("graph_surface", "graph_api", "briefing")


def test_core_dependencies_stay_minimal() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    core = {dep.split(">=")[0].split("<")[0].split("[")[0] for dep in pyproject["project"]["dependencies"]}
    assert core == {"pydantic", "PyYAML", "python-magic"}, (
        f"core dependency creep: {sorted(core)} — heavy deps belong in optional extras"
    )
    extras = pyproject["project"]["optional-dependencies"]
    assert extras, "optional extras must exist for heavy features"


def _imports_of(module_name: str) -> set[str]:
    path = REPO / "src" / "constellation" / f"{module_name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_offline_surface_modules_import_stdlib_and_constellation_only() -> None:
    for module in _OFFLINE_MODULES:
        foreign = _imports_of(module) - _STDLIB - {"constellation"}
        assert not foreign, f"{module} gained non-stdlib imports: {sorted(foreign)}"


def test_offline_surface_modules_have_no_network_calls() -> None:
    banned = ("urllib", "http", "requests", "socket", "httpx", "aiohttp")
    for module in _OFFLINE_MODULES:
        source = (REPO / "src" / "constellation" / f"{module}.py").read_text(encoding="utf-8")
        for token in banned:
            assert f"import {token}" not in source, f"{module} imports network module {token}"
            assert f"from {token}" not in source, f"{module} imports network module {token}"
