---
title: OpenCTI connector contract (import-only)
date: 2026-07-31
status: active
sensitivity: public
---

# OpenCTI Connector Contract (Import-Only)

## Scope

Constellation accepts OpenCTI **file exports** (STIX 2.x bundles, JSON)
through a bounded intake contract. There is no live OpenCTI integration:
no platform API client, no STIX/TAXII server, no feed daemon, no egress of
any kind. An operator exports a bundle from their own OpenCTI instance and
hands the file to the intake path.

## Contract

Validated by `constellation.connector_contracts.validate_opencti_bundle`
(contract tests: `tests/test_connector_contracts.py`, fictional fixture:
`tests/fixtures/connectors/opencti-sample.json`).

- Payload MUST be a STIX `bundle` object with an `objects` list.
- `identity` objects REQUIRE `id` and `name`; `identity_class` maps to the
  entity kind hint (default `organization`).
- `relationship` objects REQUIRE `id`, `relationship_type`, `source_ref`,
  `target_ref`.
- Malformed payloads fail closed with `ConnectorContractError` — no partial
  normalization, no guessing.
- Output is the normalized intermediate shape
  `{entities, relationships, provenance}`, deterministic for identical
  input bytes.

## Provenance and identity

External IDs (`identity--…`, `relationship--…`) are unstable across
OpenCTI instances and re-exports. They are carried as `external_id` and
map to the canonical `external_ids` mechanism at staging time. A canonical
Constellation ULID is NEVER minted from external IDs, and re-importing the
same external record MUST resolve to the existing canonical record (the
external_ids dedup rule), never duplicate an entity.

## Licensing / boundary notes

- OpenCTI core is Apache-2.0; its Enterprise Edition and certain
  ecosystem connectors are proprietary. This contract uses only the public
  STIX 2.x document shape — no OpenCTI code, schema files, or EE features
  are copied or required.
- OpenCTI is platform-first: its value is the full platform, not the file
  format. Constellation deliberately implements only the file-intake
  contract; operators wanting a live platform should run OpenCTI itself.
- Recorded in `docs/references/graph-intelligence-sources.md`.
