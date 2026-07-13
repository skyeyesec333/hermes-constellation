import json
from pathlib import Path

from constellation.cli import main


def invoke(capsys, *args):
    assert main(list(args)) == 0
    return json.loads(capsys.readouterr().out)


def test_cli_runs_offline_trusted_loop(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    initialized = invoke(capsys, "init", str(vault))
    assert initialized["ok"] is True
    assert (vault / "Inbox/Files").is_dir()

    source = vault / "Inbox/Files/brief.txt"
    source.write_text("Fictional cobalt logistics evidence.", encoding="utf-8")

    health = invoke(capsys, "doctor", str(vault))
    assert health["result"]["vault"]["initialized"] is True

    ingested = invoke(capsys, "ingest", str(vault), str(source))
    assert ingested["result"]["status"] == "staged"
    assert not (vault / ingested["result"]["source_item_path"]).exists()

    candidates = invoke(capsys, "review", str(vault), "list")
    assert len(candidates["result"]) == 1
    candidate_id = candidates["result"][0]["id"]
    promoted = invoke(
        capsys,
        "review",
        str(vault),
        "promote",
        "--candidate",
        candidate_id,
        "--confirm",
    )
    assert promoted["result"]["status"] == "promoted"
    assert promoted["result"]["index_generation"]

    validation = invoke(capsys, "validate", str(vault))
    assert validation["result"]["valid"] >= 1
    assert validation["result"]["invalid"] == 0

    result = invoke(capsys, "search", str(vault), "cobalt logistics")
    assert result["result"]["status"] == "evidence_found"
    assert result["result"]["evidence"][0]["path"].startswith("source-items/")
