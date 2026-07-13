# Hermes Constellation

A local-first, source-grounded knowledge and relationship workspace for Hermes Agent. Markdown notes and preserved source files remain canonical; indexes are disposable.

> Status: clean-room `0.1.0a1` local alpha. No public repository or package has been published.

## Trusted loop

```text
init → ingest → review candidate → explicit promotion → index → evidence search → research receipt
```

Collectors cannot silently change canonical meaning. Canonical writes require validation, explicit confirmation, expected-hash conflict checks, and an action-ledger entry.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

.venv/bin/constellation init ~/my-constellation
.venv/bin/constellation doctor ~/my-constellation
.venv/bin/constellation ingest ~/my-constellation ~/my-constellation/Inbox/Files/example.txt
.venv/bin/constellation review ~/my-constellation list
# Review the listed target/hash, then explicitly accept or promote it:
.venv/bin/constellation review ~/my-constellation promote \
  --candidate <candidate-id> --expected-base-hash <sha256> --confirm
.venv/bin/constellation validate ~/my-constellation
.venv/bin/constellation index ~/my-constellation
.venv/bin/constellation search ~/my-constellation "example question"

# Read-only legacy-vault inventory; this performs no writes
.venv/bin/constellation migrate-plan /path/to/legacy-vault > migration-plan.private.json

# Destination-only rehearsal; output must not exist and remains private
.venv/bin/constellation migrate-rehearse /path/to/legacy-vault /tmp/constellation-rehearsal \
  --confirm-disposable

# After backup verification and mapping review, prepare a same-filesystem sibling
.venv/bin/constellation migrate-prepare /path/to/legacy-vault /tmp/constellation-rehearsal \
  /path/to/legacy-vault.prepared --expected-source-sha256 <approved-sha256> \
  --confirm-apply-staging

# Stop every vault writer before the short atomic cutover; the original is retained
.venv/bin/constellation migrate-activate /path/to/legacy-vault \
  /path/to/legacy-vault.prepared /path/to/legacy-vault.pre-migration \
  --expected-source-sha256 <approved-sha256> --confirm-canonical-apply
```

`migrate-activate` is deliberately not a convenience command: read [`docs/migration.md`](docs/migration.md), verify a fresh restore, pause all writers, and retain the rollback vault until dogfooding succeeds.

The filesystem plugin entry point is the repository root. When installed as a Hermes plugin it exposes:

- five bounded JSON tools under the `constellation` toolset;
- `hermes constellation ...`;
- `/constellation ...`;
- the explicitly loadable skill `constellation:constellation`.

## Current capabilities

- Strict versioned Python record models and generated JSON Schema/templates.
- Safe vault initialization, contained paths, expected hashes, and atomic local writes.
- Deterministic text/Markdown ingestion with SHA-256 manifests and preserved originals.
- Optional PDF extraction through PyMuPDF when installed.
- Candidate review, including visible ingest-review packets, and expected-hash conflict-safe acceptance/promotion.
- Exact-ID and SQLite FTS5 retrieval with sensitivity ceilings and bounded evidence packets.
- Canonical-only generated `INDEX.md` plus automatic pruning of inactive SQLite index generations.
- Token-aware research receipts with a locked 25% synthesis/evaluation reserve.
- Read-only legacy-vault inventory and bounded dry-run migration plans.
- One-way allowlist release compiler and exact-tree privacy scanner.
- Entirely fictional `example.test` demo vault.

## Planned adapters

The core remains usable without network services. Optional adapters will be added after the trusted loop is stable:

- Docling and OCRmyPDF for difficult documents;
- SearXNG for discovery;
- Firecrawl for clean web extraction;
- Hermes browser/Camofox for dynamic pages;
- scholarly metadata through OpenAlex, Crossref, and Semantic Scholar;
- optional semantic retrieval after exact/FTS evaluation gates pass.

## Privacy boundary

The public distribution is compiled from an explicit lineage allowlist. Real vaults, private migration mappings, browser state, credentials, logs, indexes, embeddings, model transcripts, and private-derived fixtures are excluded.

See:

- `docs/architecture.md`
- `docs/threat-model.md`
- `docs/token-aware-research.md`
- `docs/clean-room-release.md`
- `docs/migration.md`
