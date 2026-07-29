import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from constellation.cli import main
from constellation.operator import OperatorContext, OperatorContextError, activate_operator_context


def invoke(capsys, *args):
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)["result"]


def test_operator_stage_creates_a_local_draft_without_revealing_profile_contents(
    tmp_path: Path, capsys
):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))
    profile = tmp_path / "operator.yaml"
    profile.write_text(
        yaml.safe_dump(
            {
                "roles": ["Fictional CEO"],
                "sectors": ["Fictional clean energy"],
                "strategic_priorities": ["Fictional regional partnerships"],
            }
        ),
        encoding="utf-8",
    )

    result = invoke(capsys, "operator", str(vault), "stage", "--input", str(profile))

    assert result == {"status": "draft", "version": 1}
    stored = vault / ".constellation/operator-context.yaml"
    assert stored.is_file()
    assert "Fictional CEO" not in json.dumps(result)


def test_operator_activation_requires_explicit_confirmation(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))
    profile = tmp_path / "operator.yaml"
    profile.write_text(yaml.safe_dump({"roles": ["Fictional CEO"]}), encoding="utf-8")
    invoke(capsys, "operator", str(vault), "stage", "--input", str(profile))

    with pytest.raises(OperatorContextError, match="explicit confirmation"):
        activate_operator_context(vault, confirm=False)


def test_operator_activation_marks_a_staged_profile_active_and_records_review_time(
    tmp_path: Path, capsys
):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))
    profile = tmp_path / "operator.yaml"
    profile.write_text(yaml.safe_dump({"roles": ["Fictional CEO"]}), encoding="utf-8")
    invoke(capsys, "operator", str(vault), "stage", "--input", str(profile))

    active = activate_operator_context(vault, confirm=True)

    assert active.status == "active"
    assert active.reviewed_at is not None
    stored = yaml.safe_load((vault / ".constellation/operator-context.yaml").read_text())
    assert stored["status"] == "active"
    assert stored["reviewed_at"]


def test_operator_cli_activation_reports_status_without_revealing_profile_contents(
    tmp_path: Path, capsys
):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))
    profile = tmp_path / "operator.yaml"
    profile.write_text(yaml.safe_dump({"roles": ["Fictional CEO"]}), encoding="utf-8")
    invoke(capsys, "operator", str(vault), "stage", "--input", str(profile))

    result = invoke(capsys, "operator", str(vault), "activate", "--confirm")

    assert result["status"] == "active"
    assert result["version"] == 1
    assert "Fictional CEO" not in json.dumps(result)


def test_operator_status_reads_a_persisted_active_context(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))
    profile = tmp_path / "operator.yaml"
    profile.write_text(yaml.safe_dump({"roles": ["Fictional CEO"]}), encoding="utf-8")
    invoke(capsys, "operator", str(vault), "stage", "--input", str(profile))
    invoke(capsys, "operator", str(vault), "activate", "--confirm")

    status = invoke(capsys, "operator", str(vault), "status")

    assert status == {"status": "active", "version": 1}
    assert "Fictional CEO" not in json.dumps(status)


def test_active_operator_context_requires_a_review_timestamp():
    with pytest.raises(ValidationError, match="reviewed_at"):
        OperatorContext(status="active")


def test_operator_status_reports_an_absent_profile_without_creating_one(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))

    result = invoke(capsys, "operator", str(vault), "status")

    assert result == {"status": "absent"}
    assert not (vault / ".constellation/operator-context.yaml").exists()

    profile = tmp_path / "operator.yaml"
    profile.write_text(yaml.safe_dump({"roles": ["Fictional CEO"]}), encoding="utf-8")
    invoke(capsys, "operator", str(vault), "stage", "--input", str(profile))

    draft = invoke(capsys, "operator", str(vault), "status")

    assert draft == {"status": "draft", "version": 1}
    assert "Fictional CEO" not in json.dumps(draft)


def test_operator_delete_requires_confirmation_and_removes_the_local_profile(
    tmp_path: Path, capsys
):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))
    profile = tmp_path / "operator.yaml"
    profile.write_text(yaml.safe_dump({"roles": ["Fictional CEO"]}), encoding="utf-8")
    invoke(capsys, "operator", str(vault), "stage", "--input", str(profile))

    # CLI boundary converts the confirmation error into a JSON envelope
    capsys.readouterr()
    rc = main(["operator", str(vault), "delete"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "explicit confirmation" in payload["error"]

    result = invoke(capsys, "operator", str(vault), "delete", "--confirm")

    assert result == {"status": "absent"}
    assert not (vault / ".constellation/operator-context.yaml").exists()


def test_doctor_reports_operator_profile_lifecycle_without_profile_contents(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    invoke(capsys, "init", str(vault))

    absent = invoke(capsys, "doctor", str(vault))

    assert absent["operator_context"] == {"status": "absent"}
    profile = tmp_path / "operator.yaml"
    profile.write_text(yaml.safe_dump({"roles": ["Fictional CEO"]}), encoding="utf-8")
    invoke(capsys, "operator", str(vault), "stage", "--input", str(profile))

    draft = invoke(capsys, "doctor", str(vault))

    assert draft["operator_context"] == {"status": "draft", "version": 1}
    assert "Fictional CEO" not in json.dumps(draft)
