"""Wave 5: CLI operator errors must be JSON envelopes, not raw tracebacks."""

from __future__ import annotations

import json
from pathlib import Path

from constellation.cli import main as cli_main
from constellation.vault import initialize_vault


def test_cli_error_returns_json_envelope(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    outside = tmp_path / "outside.md"
    outside.write_text("# outside the vault\n", encoding="utf-8")

    rc = cli_main(["ingest", str(vault), str(outside)])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "inside the vault" in payload["error"]
    assert "Traceback" not in payload["error"]


def test_cli_success_envelope_unchanged(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    capsys.readouterr()

    rc = cli_main(["validate", str(vault)])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["version"] == 1
