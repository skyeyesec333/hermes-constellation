import json
from pathlib import Path

import yaml
import pytest

from constellation.egress import EgressDenied, EgressRequest, authorize_egress, require_egress
from constellation.vault import initialize_vault


SOURCE_HASH = "a" * 64


def _set_provider_policy(
    vault: Path,
    *,
    transport: str,
    external_enabled: bool,
    max_sensitivity: str = "restricted",
) -> None:
    config_path = vault / ".constellation/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["egress"] = {
        "external_enabled": external_enabled,
        "providers": {
            "test-provider": {
                "enabled": True,
                "transport": transport,
                "max_sensitivity": max_sensitivity,
                "models": ["fictional-model-v1"],
                "purposes": ["stage1"],
            }
        },
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")


def _request(**overrides: str) -> EgressRequest:
    values = {
        "provider": "test-provider",
        "model": "fictional-model-v1",
        "purpose": "stage1",
        "sensitivity": "internal",
    }
    values.update(overrides)
    return EgressRequest(**values, source_hashes=(SOURCE_HASH,))


def test_missing_egress_policy_denies_and_records_the_attempt(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config_path = vault / ".constellation/config.yaml"
    legacy_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    legacy_config.pop("egress", None)
    config_path.write_text(yaml.safe_dump(legacy_config, sort_keys=True), encoding="utf-8")

    decision = authorize_egress(
        vault,
        EgressRequest(
            provider="local-test",
            model="fictional-model-v1",
            purpose="stage1",
            sensitivity="internal",
            source_hashes=(SOURCE_HASH,),
        ),
    )

    assert decision.allowed is False
    assert decision.reason == "egress_not_configured"
    assert decision.policy_sha256
    assert decision.request_sha256
    ledger = vault / ".constellation/egress-ledger.jsonl"
    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["allowed"] is False
    assert events[0]["request_sha256"] == decision.request_sha256
    assert events[0]["source_hashes"] == [SOURCE_HASH]


def test_explicit_local_provider_allows_only_declared_model_and_purpose(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _set_provider_policy(vault, transport="local", external_enabled=False)

    decision = authorize_egress(
        vault,
        _request(sensitivity="restricted"),
    )

    assert decision.allowed is True
    assert decision.reason == "allowed"
    assert decision.transport == "local"


def test_external_provider_requires_global_opt_in(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _set_provider_policy(vault, transport="external", external_enabled=False)

    denied = authorize_egress(vault, _request())
    assert denied.allowed is False
    assert denied.reason == "external_egress_disabled"

    _set_provider_policy(vault, transport="external", external_enabled=True)
    allowed = authorize_egress(vault, _request())
    assert allowed.allowed is True
    assert allowed.transport == "external"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"model": "undeclared-model"}, "model_not_allowed"),
        ({"purpose": "research"}, "purpose_not_allowed"),
        ({"sensitivity": "confidential"}, "sensitivity_exceeds_policy"),
    ],
)
def test_policy_restricts_model_purpose_and_sensitivity(
    tmp_path: Path, overrides: dict[str, str], reason: str
):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _set_provider_policy(
        vault,
        transport="external",
        external_enabled=True,
        max_sensitivity="internal",
    )

    decision = authorize_egress(vault, _request(**overrides))

    assert decision.allowed is False
    assert decision.reason == reason


def test_malformed_policy_fails_closed(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config_path = vault / ".constellation/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["egress"] = {"external_enabled": "yes", "providers": {}}
    config_path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")

    decision = authorize_egress(vault, _request())

    assert decision.allowed is False
    assert decision.reason == "policy_invalid"


def test_require_egress_raises_after_recording_a_denial(tmp_path: Path):
    vault = tmp_path / "vault"
    initialize_vault(vault)

    with pytest.raises(EgressDenied, match="provider_not_declared"):
        require_egress(vault, _request())

    ledger = vault / ".constellation/egress-ledger.jsonl"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
