# Hermes Constellation

A local-first, source-grounded knowledge and relationship workspace for Hermes Agent. Markdown notes and preserved source files remain canonical; indexes are disposable.

> Status: clean-room `0.1.0a1` local alpha. No public repository or package has been published.

## Trusted loop

```text
init → ingest → automatic or reviewed source registration → automatic index → evidence search → research receipt
```

Vault policy may automatically register an extracted source record because it preserves mechanical evidence rather than derived meaning. Claims, entities, research conclusions, and source updates remain candidate-reviewed, conflict-checked canonical changes.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

.venv/bin/constellation init ~/my-constellation
.venv/bin/constellation doctor ~/my-constellation
.venv/bin/constellation ingest ~/my-constellation ~/my-constellation/Inbox/Files/example.txt
.venv/bin/constellation review ~/my-constellation list
# New sources are create-only candidates: inspect, then explicitly promote.
# Promotion writes canonical Markdown and rebuilds the index.
.venv/bin/constellation review ~/my-constellation promote \
  --candidate <candidate-id> --confirm
# Updates additionally require --expected-base-hash <reviewed-sha256>.
.venv/bin/constellation validate ~/my-constellation
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
- Deterministic text/Markdown ingestion with SHA-256 manifests, preserved originals, and configurable reviewed or automatic mechanical source registration.
- Optional PDF extraction through PyMuPDF with native page anchors and local RapidOCR fallback for scanned pages.
- Optional local DOCX, PPTX, and XLSX extraction with paragraph, table, slide, speaker-note, sheet, and cell anchors.
- Optional local image/business-card OCR with confidence scores and region bounding boxes.
- Required libmagic MIME detection plus bounded OOXML entry count, expanded size, compression ratio, member paths, encryption, and internal content-type checks.
- Automatic source registration through the same validation/action-ledger/index path, or create-only source review by policy; meaning-bearing updates remain conflict-safe candidates.
- Exact-ID and SQLite FTS5 retrieval with sensitivity ceilings and page/line-aware evidence packets.
- Strict entity kinds, evidence state, and conflict-safe merge metadata; automatic entity matching is not included.
- Canonical-only generated `INDEX.md` plus automatic pruning of inactive SQLite index generations.
- Versioned research receipts with provider/model identity, measured-versus-estimated token accounting, evidence hashes, retries, and terminal promotion policy.
- Deny-by-default model egress authorization with exact provider/model/purpose/sensitivity policy and a durable decision ledger.
- Read-only legacy-vault inventory and bounded dry-run migration plans.
- One-way allowlist release compiler and exact-tree privacy scanner.
- Entirely fictional `example.test` demo vault.

## Integrations and roadmap

Implemented optional local adapters are PyMuPDF, RapidOCR/Pillow, python-docx, python-pptx, openpyxl, and MarkItDown for manual PPTX fallback. Docling/Marker, Hermes Camofox/browser capture, Firecrawl, SearXNG, scholarly APIs, automatic model-driven Stage 1, and semantic/vector retrieval are **planned, not shipped** in the public package.

See [`docs/integrations.md`](docs/integrations.md) for the supported/optional/planned capability matrix and the local-versus-network privacy boundary for each tool.

## Privacy boundary

The public distribution is compiled from an explicit lineage allowlist. Real vaults, private migration mappings, browser state, credentials, logs, indexes, embeddings, model transcripts, and private-derived fixtures are excluded.

See:

- `docs/architecture.md`
- `docs/integrations.md`
- `docs/egress-policy.md`
- `docs/threat-model.md`
- `docs/token-aware-research.md`
- `docs/clean-room-release.md`
- `docs/migration.md`
