"""Tests for research infrastructure health probes."""

import json
import urllib.error
from pathlib import Path

import pytest

from constellation.research_health import probe_research_health
from constellation.vault import initialize_vault


@pytest.fixture(autouse=True)
def _block_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("live network disabled in tests")

    monkeypatch.setattr("urllib.request.urlopen", blocked_urlopen)


def test_health_probe_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    vault = tmp_path / "vault"
    initialize_vault(vault)

    result = probe_research_health(vault)
    assert result["status"] in ("healthy", "degraded")
    probes = result["probes"]
    hints = result["recovery_hints"]
    assert isinstance(probes, dict)
    assert isinstance(probes["exa"], dict)
    assert isinstance(probes["brave_api"], dict)
    assert probes["exa"]["configured"] is False
    assert probes["brave_api"]["configured"] is False
    assert isinstance(hints, list)
    assert "Set EXA_API_KEY for semantic search (CAPTCHA-immune)" in hints
    assert "Set BRAVE_API_KEY for time-sensitive search fallback" in hints


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
