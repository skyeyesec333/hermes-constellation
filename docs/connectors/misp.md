---
title: MISP connector contract (import-only)
date: 2026-07-31
status: active
sensitivity: public
---

# MISP Connector Contract (Import-Only)

## Scope

Constellation accepts MISP **event file exports** (JSON) through a bounded
intake contract. There is no live MISP integration: no PyMISP client, no
feed synchronization, no community publishing, no ZMQ/event-stream
consumption, no egress of any kind.

## Contract

Validated by `constellation.connector_contracts.validate_misp_event`
(contract tests: `tests/test_connector_contracts.py`, fictional fixture:
`tests/fixtures/connectors/misp-sample.json`).

- Payload MUST carry an `Event` object with a `uuid`.
- `Attribute` entries REQUIRE `uuid` and `value`. An attribute denotes an
  entity when its `comment` carries the `entity:<kind>` marker.
- `Object[].ObjectReference[]` entries with `relationship_type`,
  `source_uuid`, and `target_uuid` denote relationships.
- Malformed payloads fail closed with `ConnectorContractError`.
- Output is the normalized intermediate shape
  `{entities, relationships, provenance}`, deterministic for identical
  input bytes.

## Provenance and identity

MISP UUIDs are unstable across instances and re-exports. They are carried
as `external_id` and map to the canonical `external_ids` mechanism at
staging time — never minted into canonical ULIDs, never duplicating
entities on re-import.

## Licensing / boundary notes

- MISP core is AGPL-3.0. Constellation does NOT copy, link, or reuse MISP
  core code, taxonomies, or galaxy files; the contract uses only the
  documented event JSON shape from operator exports. AGPL coverage of
  server-side publishing/community features is out of scope by design —
  those are deliberately not implemented.
- MISP's communities, publishing, and feed network overlap with what an
  intelligence operation might want, but Constellation's wedge is
  trust/auditability of a private evidence graph, not threat-intel sharing
  infrastructure. Recorded in
  `docs/references/graph-intelligence-sources.md`.
