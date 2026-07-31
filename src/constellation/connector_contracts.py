"""Import-only connector contracts (i2-successor Wave 6 Task 6.2).

File-intake contracts for OpenCTI-style STIX bundles and MISP events.
There are no STIX/TAXII servers, no feed daemons, and no live egress —
connectors normalize already-downloaded JSON into a stable intermediate
shape that staging code (e.g. the FtM adapter pattern) can review-gate.

Contract rules:

- malformed payloads fail closed with ConnectorContractError, never raise
  raw KeyErrors or silently guess;
- unstable external IDs map to ``external_id`` (canonical provenance via
  the external_ids mechanism at staging time) — a canonical ULID is never
  minted from external data;
- output is deterministic for identical input bytes.
"""

from __future__ import annotations

from typing import Any


class ConnectorContractError(RuntimeError):
    """Raised when a connector payload violates the intake contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConnectorContractError(message)


def validate_opencti_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize an OpenCTI-style STIX 2.x bundle (file export)."""
    _require(isinstance(payload, dict), "opencti payload must be an object")
    _require(payload.get("type") == "bundle", "opencti payload must be a STIX bundle")
    objects = payload.get("objects")
    _require(isinstance(objects, list), "opencti bundle must carry an objects list")

    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    for obj in objects:
        _require(isinstance(obj, dict) and obj.get("type"), "bundle object missing type")
        if obj["type"] == "identity":
            _require(bool(obj.get("id")) and bool(obj.get("name")),
                     "identity requires id and name")
            entities.append({
                "external_id": str(obj["id"]),
                "name": str(obj["name"]),
                "kind": str(obj.get("identity_class", "organization")),
            })
        elif obj["type"] == "relationship":
            _require(
                bool(obj.get("id")) and bool(obj.get("relationship_type"))
                and bool(obj.get("source_ref")) and bool(obj.get("target_ref")),
                "relationship requires id, relationship_type, source_ref, target_ref",
            )
            relationships.append({
                "external_id": str(obj["id"]),
                "relationship_type": str(obj["relationship_type"]),
                "source_ref": str(obj["source_ref"]),
                "target_ref": str(obj["target_ref"]),
            })
    entities.sort(key=lambda e: e["external_id"])
    relationships.sort(key=lambda r: r["external_id"])
    return {
        "connector": "opencti",
        "entities": entities,
        "relationships": relationships,
        "provenance": {
            "external_namespace": "opencti",
            "bundle_id": str(payload.get("id", "")),
        },
    }


def validate_misp_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a MISP event (file export). Entity attributes carry an
    ``entity:<kind>`` comment marker; object references carry relationships."""
    _require(isinstance(payload, dict), "misp payload must be an object")
    event = payload.get("Event")
    _require(isinstance(event, dict), "misp payload must carry an Event object")
    _require(bool(event.get("uuid")), "misp event requires a uuid")

    attributes = event.get("Attribute", [])
    _require(isinstance(attributes, list), "misp Attribute must be a list")
    objects = event.get("Object", [])
    _require(isinstance(objects, list), "misp Object must be a list")

    entities: list[dict[str, Any]] = []
    for attr in attributes:
        _require(isinstance(attr, dict) and attr.get("uuid") and attr.get("value") is not None,
                 "misp attribute requires uuid and value")
        comment = str(attr.get("comment", ""))
        kind = comment.split(":", 1)[1] if comment.startswith("entity:") else ""
        if kind:
            entities.append({
                "external_id": str(attr["uuid"]),
                "name": str(attr["value"]),
                "kind": kind,
            })

    relationships: list[dict[str, Any]] = []
    for obj in objects:
        _require(isinstance(obj, dict), "misp object must be an object")
        references = obj.get("ObjectReference", [])
        _require(isinstance(references, list), "misp ObjectReference must be a list")
        for ref in references:
            _require(isinstance(ref, dict), "misp reference must be an object")
            if ref.get("relationship_type") and ref.get("source_uuid") and ref.get("target_uuid"):
                relationships.append({
                    "external_id": str(ref.get("uuid", "")),
                    "relationship_type": str(ref["relationship_type"]),
                    "source_ref": str(ref["source_uuid"]),
                    "target_ref": str(ref["target_uuid"]),
                })
    entities.sort(key=lambda e: e["external_id"])
    relationships.sort(key=lambda r: r["external_id"])
    return {
        "connector": "misp",
        "entities": entities,
        "relationships": relationships,
        "provenance": {
            "external_namespace": "misp",
            "event_uuid": str(event["uuid"]),
        },
    }


_VALIDATORS = {
    "opencti": validate_opencti_bundle,
    "misp": validate_misp_event,
}


def normalize_connector(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch to a connector validator by name."""
    validator = _VALIDATORS.get(kind)
    if validator is None:
        raise ConnectorContractError(f"unknown connector: {kind}")
    return validator(payload)
