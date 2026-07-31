"""Contract tests for import-only connector normalization (Wave 6 Task 6.2).

Connectors are file-intake contracts only — no STIX/TAXII servers, no feed
daemons, no live egress. Unstable external IDs always map to canonical
provenance (external_ids), never to canonical ULIDs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from constellation.connector_contracts import (
    ConnectorContractError,
    normalize_connector,
    validate_misp_event,
    validate_opencti_bundle,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "connectors"


def test_opencti_fixture_normalizes() -> None:
    payload = json.loads((FIXTURES / "opencti-sample.json").read_text(encoding="utf-8"))
    result = validate_opencti_bundle(payload)

    assert result["connector"] == "opencti"
    assert len(result["entities"]) == 2
    names = {e["name"] for e in result["entities"]}
    assert names == {"Fictional Alpha Ltd", "Fictional Beta SA"}
    assert all(e["external_id"].startswith("identity--") for e in result["entities"])
    assert len(result["relationships"]) == 1
    rel = result["relationships"][0]
    assert rel["relationship_type"] == "owns"
    assert rel["source_ref"] == "identity--fictional-aaa"
    assert rel["target_ref"] == "identity--fictional-bbb"
    assert result["provenance"]["external_namespace"] == "opencti"


def test_misp_fixture_normalizes() -> None:
    payload = json.loads((FIXTURES / "misp-sample.json").read_text(encoding="utf-8"))
    result = validate_misp_event(payload)

    assert result["connector"] == "misp"
    assert len(result["entities"]) == 2
    assert len(result["relationships"]) == 1
    rel = result["relationships"][0]
    assert rel["relationship_type"] == "owns"
    assert result["provenance"]["external_namespace"] == "misp"
    assert result["provenance"]["event_uuid"] == "fictional-misp-event-0001"


def test_unstable_ids_never_become_canonical() -> None:
    payload = json.loads((FIXTURES / "opencti-sample.json").read_text(encoding="utf-8"))
    result = validate_opencti_bundle(payload)
    for entity in result["entities"]:
        # External IDs stay in the external_id slot; no canonical ULID is minted.
        assert "id" not in entity or entity.get("id") is None
        assert entity["external_id"]


def test_normalize_dispatch() -> None:
    payload = json.loads((FIXTURES / "misp-sample.json").read_text(encoding="utf-8"))
    result = normalize_connector("misp", payload)
    assert result["connector"] == "misp"
    with pytest.raises(ConnectorContractError):
        normalize_connector("taxii", payload)


def test_malformed_payloads_fail_closed() -> None:
    with pytest.raises(ConnectorContractError):
        validate_opencti_bundle({"type": "bundle", "objects": [{"type": "identity"}]})
    with pytest.raises(ConnectorContractError):
        validate_opencti_bundle({"objects": []})
    with pytest.raises(ConnectorContractError):
        validate_misp_event({"Event": {"Attribute": "not-a-list"}})
    with pytest.raises(ConnectorContractError):
        validate_misp_event({"no_event": True})
