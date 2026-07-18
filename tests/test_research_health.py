"""Tests for research infrastructure health probes."""

import json
from pathlib import Path

from constellation.research_health import probe_research_health
from constellation.vault import initialize_vault


def test_health_probe_runs(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    result = probe_research_health(vault)
    assert result["status"] in ("healthy", "degraded")
    assert "probes" in result
    assert "recovery_hints" in result


def test_health_state_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    probe_research_health(vault)
    state_path = vault / ".constellation/state/research_health.json"
    assert state_path.is_file()

    state = json.loads(state_path.read_text())
    assert state["status"] in ("healthy", "degraded")
    assert "last_probe" in state


def test_health_transition_detected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    # First run
    r1 = probe_research_health(vault)
    assert "transition" in r1

    # Second run: no transition (same status)
    r2 = probe_research_health(vault)
    assert r2["transition"] is None


def test_health_cli_args() -> None:
    from constellation.cli import build_parser

    values = vars(build_parser().parse_args(["health", "/tmp/vault"]))
    assert values["command"] == "health"
