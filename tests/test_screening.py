"""Tests for Stage 7.6 ingest-time secret/PII screening.

Contract: local, deterministic, zero-egress pre-ingest scan. Findings are
BLOCKED (strict default) or quarantined-with-warning per vault profile —
content is never silently stripped or altered. Complements, never replaces,
the release-time privacy audit.
"""

from pathlib import Path

import pytest
import yaml

from constellation.ingest import IngestError, ingest_file
from constellation.screening import screen_text
from constellation.vault import initialize_vault

CLEAN = "Quarterly report: revenue grew 12 percent across all regions.\n"
# Planted fixtures are concatenated so the release privacy audit never sees
# a literal secret/PII pattern in this file (established test_privacy.py pattern).
AWS_KEY = "aws_access_key_id = " + "AKIA" + "IOSFODNN7" + "EXAMPLE" + "\n"
PRIV_KEY = (
    "-----BEGIN RSA " + "PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA7\n"
    "-----END RSA " + "PRIVATE KEY-----\n"
)
SECRET_ASSIGN = "api" + '_key = "sk-' + "abcdefghij0123456789" * 2 + '"\n'
PII = "Contact " + "somchai" + "@" + "example.co.th" + " or " + "+66" + "812345678 for details.\n"


def test_clean_text_has_no_findings() -> None:
    assert screen_text(CLEAN) == []


def test_aws_key_is_secret_finding() -> None:
    findings = screen_text(AWS_KEY)
    assert any(f["rule"] == "known_token" and f["severity"] == "secret" for f in findings)


def test_private_key_block_is_secret_finding() -> None:
    findings = screen_text(PRIV_KEY)
    assert any(f["rule"] == "private_key" and f["severity"] == "secret" for f in findings)


def test_secret_assignment_is_secret_finding() -> None:
    findings = screen_text(SECRET_ASSIGN)
    assert any(f["rule"] == "secret_assignment" and f["severity"] == "secret" for f in findings)


def test_email_and_phone_are_pii_findings() -> None:
    findings = screen_text(PII)
    rules = {f["rule"] for f in findings}
    assert "email" in rules
    assert "phone" in rules
    assert all(f["severity"] == "pii" for f in findings)


def test_findings_never_include_the_secret_itself() -> None:
    findings = screen_text(AWS_KEY + PRIV_KEY + SECRET_ASSIGN)
    for finding in findings:
        assert "AKIA" + "IOSFODNN7" not in str(finding)
        assert "sk-" + "abcdefghij" not in str(finding)
        assert "MIIEpAIBAAKCAQEA7" not in str(finding)


# ── ingest integration ─────────────────────────────────────────────────────


def _vault(tmp_path: Path, screening: str | None = None) -> Path:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    if screening is not None:
        config_path = vault / ".constellation" / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["ingest_screening"] = screening
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return vault


def _write_source(vault: Path, text: str, name: str = "intake.txt") -> Path:
    path = vault / name
    path.write_text(text, encoding="utf-8")
    return path


def test_strict_default_blocks_planted_secret(tmp_path: Path) -> None:
    vault = _vault(tmp_path)  # no key -> strict default
    source = _write_source(vault, "Report\n\n" + AWS_KEY)

    with pytest.raises(IngestError, match="screening|secret|blocked"):
        ingest_file(vault, source)
    assert not list((vault / "Library" / "Files").rglob("intake.txt"))  # nothing preserved


def test_quarantine_proceeds_with_warning(tmp_path: Path) -> None:
    vault = _vault(tmp_path, screening="quarantine")
    source = _write_source(vault, "Report\n\n" + AWS_KEY)

    result = ingest_file(vault, source)

    assert result["status"] in {"staged", "already_ingested"}
    assert result["screening"]["policy"] == "quarantine"
    assert "known_token" in result["screening"]["rules"]


def test_off_disables_screening(tmp_path: Path) -> None:
    vault = _vault(tmp_path, screening="off")
    source = _write_source(vault, "Report\n\n" + AWS_KEY)

    result = ingest_file(vault, source)

    assert result["status"] in {"staged", "already_ingested"}
    assert "screening" not in result


def test_clean_file_unaffected_in_all_modes(tmp_path: Path) -> None:
    for mode in (None, "quarantine", "off"):
        vault = _vault(tmp_path / str(mode), screening=mode)
        source = _write_source(vault, CLEAN)
        result = ingest_file(vault, source)
        assert result["status"] in {"staged", "already_ingested"}
        assert "screening" not in result


def test_unknown_policy_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path, screening="permissive")
    source = _write_source(vault, CLEAN)

    with pytest.raises(IngestError, match="ingest_screening|screening"):
        ingest_file(vault, source)
