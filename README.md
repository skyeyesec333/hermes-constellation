# Hermes Constellation

Constellation is a local-first, source-grounded knowledge and relationship workspace for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It keeps Markdown notes and original source files as the canonical record. SQLite indexes, generated catalogs, OCR text, and model outputs can be rebuilt.

Version 0.1.0 is the first public clean-room release. It contains the deterministic vault, ingestion, review, retrieval, privacy, and Hermes plugin core. It does not contain anyone's private vault, contacts, reports, browser state, credentials, model transcripts, or host-specific automation.

## Why this exists

Most personal knowledge systems make one of two mistakes:

1. They store polished summaries without preserving the evidence that produced them.
2. They dump files into a folder and leave the human to remember what matters.

Constellation keeps the source and the interpretation separate.

A PDF, slide deck, spreadsheet, business card, or note enters as evidence. Constellation preserves the original bytes, hashes them, extracts local text where possible, and registers a source record. A model may then perform a bounded first distillation: summarize the source, identify clear entities, connect it to existing context, separate source claims from inference, and leave explicit follow-up questions. Deeper research remains a separate action.

The result is still an ordinary folder of Markdown and source files. Obsidian can open it. Git can version a sanitized vault. Another program can parse it. If Constellation disappears, the evidence does not.

## The short version

```text
file or text
  -> validate type and safety limits
  -> preserve original bytes
  -> calculate SHA-256
  -> extract local evidence with stable anchors
  -> write an extraction manifest
  -> register or stage a source-item
  -> validate canonical records
  -> rebuild disposable search index
  -> optionally run one bounded first distillation
  -> write a receipt and stop
```

The deterministic core does not require an LLM. Model synthesis is an optional layer with an explicit egress policy.

## What ships in v0.1

### Canonical local vault

- Versioned Markdown record schemas for sources, entities, claims, research runs, and candidate patches.
- Original source preservation under the vault.
- SHA-256 provenance and immutable extraction manifests.
- Atomic, path-contained writes with expected-base-hash conflict checks.
- Canonical validation and a generated human-readable `INDEX.md`.
- SQLite FTS5 retrieval that can be deleted and rebuilt from Markdown.

### Local document intake

- UTF-8 text and Markdown.
- Native and scanned PDFs.
- DOCX paragraphs and table cells.
- PPTX slide text, tables, and speaker notes.
- XLSX sheets, cells, values, and formulas.
- PNG, JPEG, WebP, TIFF, and BMP images.
- Business-card and screenshot OCR through RapidOCR.

### Evidence and safety

- Stable page, paragraph, slide, table, cell, and OCR-region anchors.
- MIME detection with libmagic instead of trusting only the extension.
- Internal OOXML content-type checks for DOCX, PPTX, and XLSX.
- Limits on source size, expanded Office archive size, archive entries, compression ratio, unsafe paths, and encrypted packages.
- OCR confidence, bounding boxes, dimensions, warnings, and partial/failure status.
- Native PDF text first, with OCR only for pages that need it.

### Controlled meaning changes

- Configurable automatic registration for mechanical source records.
- Create-only review candidates when automatic registration is disabled.
- Explicit promotion for meaning-bearing canonical changes.
- Base-hash protection against overwriting a note that changed after review.
- Action-ledger entries for promoted changes.

### Retrieval, research, and egress

- Exact-ID and FTS5 search with sensitivity ceilings.
- Evidence packets that include record ID, path, route, sensitivity, and anchor.
- Research receipts with provider/model identity, token accounting, evidence hashes, retries, and completion status.
- Deny-by-default provider/model/purpose/sensitivity egress policy.
- Durable allow/deny decisions without storing API keys in the vault.

### Public-release protection

- An explicit file-lineage allowlist.
- A one-way clean-room release compiler.
- Privacy scanning for paths, secrets, hostnames, usernames, and private canaries.
- A fictional `example.test` demo vault.

## What does not ship in v0.1

Constellation v0.1 does not pretend to be a complete autonomous research product.

The public package does not include:

- a Google Drive watcher;
- Telegram delivery;
- a mandatory model provider;
- automatic LinkedIn or web research;
- automatic entity resolution;
- a hosted sync service;
- a vector database;
- Docling or Marker model downloads;
- audio/video ingestion;
- a private user's first-distillation notes or prompts.

A private deployment can connect Drive, messaging gateways, web research, vision, or other Hermes tools around the core. Those adapters must preserve the same consent, provenance, and egress boundaries.

## Canonical records and generated artifacts

A typical vault looks like this:

```text
my-constellation/
├── .constellation/
│   ├── config.yaml
│   ├── index.sqlite3
│   ├── manifests/
│   ├── candidates/
│   ├── receipts/
│   └── action-ledger.jsonl
├── Library/
│   ├── Files/
│   └── Text/
├── Inbox/
│   └── Files/
├── source-items/
├── entities/
├── claims/
├── research/
├── HOME.md
└── INDEX.md
```

Canonical:

- original files;
- Markdown records in canonical folders;
- extraction manifests;
- receipts and action history.

Disposable and rebuildable:

- SQLite FTS indexes;
- generated `INDEX.md`;
- extracted/OCR text when the original source still exists.

Candidates are not canonical evidence until promoted.

## What happens on first upload

"First upload" is a deployment policy, not a hidden background research license.

The deterministic core performs these steps:

1. Confirm that the source is inside the allowed vault boundary.
2. Read the bytes with a configured maximum size.
3. Detect the media type and verify format-specific signatures.
4. Preserve the original under `Library/Files/`.
5. Calculate SHA-256 and use it for provenance and idempotency.
6. Extract local evidence into `Library/Text/`.
7. Create a JSON extraction manifest with engine versions, anchors, status, warnings, and hashes.
8. Register a mechanical source-item automatically or stage it for review, according to vault policy.
9. Validate canonical records and rebuild the FTS index after a canonical write.

A deployment may then authorize exactly one bounded model pass. The recommended first-distillation contract asks the model to:

- identify what the source is;
- summarize it without replacing it;
- list important source-backed facts;
- identify clearly named people, organizations, products, and projects;
- connect to existing canonical records without inventing identity matches;
- separate mechanical facts, source claims, prior-vault evidence, and inference;
- preserve conflicts instead of silently choosing a convenient version;
- classify relevance and sensitivity;
- write open questions and optional follow-up paths;
- produce a completion receipt and stop.

That pass does not automatically authorize broad web research, LinkedIn OSINT, contacting people, publishing, or repeated model calls.

See [First-distillation contract](docs/first-distillation-contract.md).

## Model-provider portability

Constellation is deliberately split so that changing providers does not change the evidence substrate.

The provider never needs to remember how Constellation was built. It needs three things in context:

1. the Constellation operating skill;
2. the first-distillation contract;
3. the evidence packet and relevant canonical records for the current source.

The local extractor, hashes, manifests, schemas, index, sensitivity policy, and action ledger remain the same across providers. Model quality can still vary. A weaker model may miss conflicts, over-create entities, blur source claims with inference, or produce a generic summary. Markdown instructions reduce that variance but cannot remove it.

For Hermes, the repository includes `skills/constellation/SKILL.md`. Load it explicitly when changing models or use it in a profile whose startup instructions require it. The skill tells the model how to operate the vault; it is more useful than a Unix man page because Hermes injects skill content into the model context.

Before making a new provider the default, run the same small evaluation set through both models and compare:

- evidence fidelity;
- claim/inference separation;
- conflict detection;
- entity restraint;
- sensitivity handling;
- usefulness of follow-up questions;
- schema and receipt compliance;
- latency and cost.

See [Model-provider portability](docs/model-provider-portability.md).

## Installation

Requirements:

- Python 3.11 or newer;
- libmagic available to `python-magic`;
- SQLite with FTS5 support;
- optional document dependencies for PDF, Office, and OCR formats.

Clone and install for development:

```bash
git clone https://github.com/skyeyesec333/hermes-constellation.git
cd hermes-constellation
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,pdf,office,ocr]'
```

Install the built wheel with all document adapters:

```bash
python -m pip install 'hermes-constellation[pdf,office,ocr] @ file:///absolute/path/to/hermes_constellation-0.1.0-py3-none-any.whl'
```

The optional dependency groups are:

```text
pdf     PyMuPDF
ocr     Pillow + RapidOCR ONNX Runtime
office  python-docx + python-pptx + openpyxl + MarkItDown PPTX fallback
```

More detail: [Installation](docs/installation.md).

## Quick start without Hermes

```bash
CONSTELLATION=./.venv/bin/constellation
VAULT="$HOME/my-constellation"

$CONSTELLATION init "$VAULT"
$CONSTELLATION doctor "$VAULT"

cp examples/synthetic-demo-vault/Inbox/Files/demo-brief.txt \
  "$VAULT/Inbox/Files/demo-brief.txt"

$CONSTELLATION ingest "$VAULT" "$VAULT/Inbox/Files/demo-brief.txt"
$CONSTELLATION validate "$VAULT"
$CONSTELLATION search "$VAULT" "Northstar Field Labs"
```

The default policy stages a create-only source candidate. Inspect and promote it:

```bash
$CONSTELLATION review "$VAULT" list
$CONSTELLATION review "$VAULT" promote \
  --candidate <candidate-id> \
  --confirm
```

For an update candidate, also supply the reviewed base hash:

```bash
$CONSTELLATION review "$VAULT" promote \
  --candidate <candidate-id> \
  --expected-base-hash <reviewed-sha256> \
  --confirm
```

Automatic mechanical source registration can be enabled in `.constellation/config.yaml`:

```yaml
kind: constellation-vault
schema_version: '0.1'
source_registration: automatic
```

This affects mechanical source records only. It does not authorize silent derived claims or arbitrary entity edits.

## Hermes plugin

The repository root is the filesystem plugin entry point. Once installed and enabled, the plugin provides:

- `constellation_status`;
- `constellation_ingest`;
- `constellation_validate`;
- `constellation_search`;
- `constellation_review`;
- `hermes constellation ...`;
- `/constellation ...`;
- the loadable skill `constellation:constellation`.

Typical setup:

```bash
python -m pip install dist/hermes_constellation-0.1.0-py3-none-any.whl
hermes plugins enable constellation
hermes plugins list
hermes doctor
```

Load the operating skill in a session:

```bash
hermes -s constellation:constellation
```

Provider selection remains a Hermes concern:

```bash
hermes model
# or, inside a session:
/model
```

Changing the provider does not move or rewrite the vault.

## Evidence anchors by format

Examples of stable anchors:

```text
PDF page                    P0007
PDF OCR region              P0007:OCR:R0003
DOCX paragraph              P0004
DOCX table cell             T0002:R0003:C0001
PPTX slide text             SLIDE0005:TEXT0002
PPTX table cell             SLIDE0005:TABLE0001:R0002:C0003
PPTX speaker notes          SLIDE0005:NOTES
XLSX cell                   SHEET0002:B17
Image OCR region            OCR:R0008
Text lines                  L000120-L000137
```

Anchors are evidence handles, not claims that the extraction is perfect. OCR confidence and warnings remain in the manifest. The original source is always the final reference.

## Search behavior

Constellation search is source-grounded retrieval, not an answer generator.

```bash
constellation search ~/my-constellation "deployment boundary"
```

A result packet includes:

- canonical note ID;
- vault-relative path;
- sensitivity;
- exact or FTS route;
- score;
- page/line-aware anchor where available.

`evidence_not_retrieved` means the current search did not retrieve evidence. It does not prove that the evidence does not exist. A stale canonical fingerprint causes retrieval to fail closed until the index is rebuilt.

## Review and conflict safety

Constellation distinguishes mechanical registration from meaning-bearing changes.

A new source can be staged as a create-only candidate. A reviewer sees the target path, candidate hash, and proposed record before promotion. Updates also include an expected base hash. If the canonical file changed after review, promotion stops rather than overwriting the newer version.

This is intentionally less convenient than letting an agent edit everything directly.

## Privacy and model egress

Local ingestion, hashing, parsing, OCR, validation, and FTS indexing do not require a model provider.

Model egress is denied unless vault policy authorizes the exact combination of:

- provider;
- model;
- purpose;
- source sensitivity.

The decision is recorded in an egress ledger. Credentials remain outside the vault.

A private deployment may treat a deliberate upload into a designated intake folder as consent for one bounded first-distillation pass. That policy must be explicit. Ordinary indexing, maintenance, or queue processing must not silently become permission to send source content to a model.

Read [Egress policy](docs/egress-policy.md), [Threat model](docs/threat-model.md), and [Privacy](PRIVACY.md) before connecting external providers.

## Obsidian and macOS

Constellation is a folder of normal files, so Obsidian can open it directly.

If the canonical vault lives on a Linux server and macOS mounts it with SSHFS, upgrading Constellation does not require a new mount. The mount points at the vault directory, not the package version. Keep the same remote and local paths unless you deliberately migrate the canonical vault.

Generic example:

```bash
mkdir -p "$HOME/constellation-vault"
sshfs user@server:/absolute/path/to/my-constellation \
  "$HOME/constellation-vault" \
  -o volname=constellation,defer_permissions,reconnect
```

Open `~/constellation-vault` as an Obsidian vault. Do not run Obsidian Sync, LiveSync, or another two-way synchronization tool against the same agent-written SSHFS mount.

## Migration

Migration is separate from normal ingestion. Constellation provides read-only inventory, destination-only rehearsal, same-filesystem preparation, and explicit atomic activation.

```bash
constellation migrate-plan /path/to/legacy-vault > migration-plan.private.json

constellation migrate-rehearse \
  /path/to/legacy-vault \
  /tmp/constellation-rehearsal \
  --confirm-disposable
```

Before activation, verify a current backup, review the mapping, stop all writers, and retain the old vault as a rollback directory. Read [Migration](docs/migration.md) before using the activation command.

## Release and privacy verification

The public tree is compiled from `resources/public-lineage.yaml`. The compiler rejects files outside the allowlist and scans the output tree before release.

```bash
export CONSTELLATION_RELEASE_CANARY='PRIVATE-CANARY-EXAMPLE'
python scripts/build_release.py \
  . \
  /tmp/hermes-constellation-public \
  resources/public-lineage.yaml \
  --canary "$CONSTELLATION_RELEASE_CANARY"
```

Then build and test from the compiled tree rather than the working repository:

```bash
python -m build /tmp/hermes-constellation-public \
  --outdir /tmp/hermes-constellation-dist
```

The release process is one-way. Never copy a private vault into a public repository and try to remove names afterward.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,pdf,office,ocr]'
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python -m build
```

The test suite covers schemas, path containment, ingestion, OCR boundaries, Office archive safety, review promotion, stale-index failure, research receipts, egress policy, packaging, plugin discovery, and clean-room release behavior.

## Known limits

- First distillation is only as good as the selected model and the context it receives.
- OCR can miss stylized text, handwriting, low-resolution scans, and diagrams.
- Native slide/PDF text does not always preserve visual relationships.
- Automatic entity matching is not implemented.
- FTS5 is lexical retrieval, not semantic retrieval.
- Encrypted Office files are rejected rather than decrypted.
- Drive, Telegram, browser research, and host-specific scheduling require deployment adapters.
- v0.1 has no audio/video intake.

## Roadmap

Likely next additions should be driven by representative failures, not package count:

- visual slide rendering through LibreOffice;
- optional OCRmyPDF/Tesseract preprocessing;
- contact normalization and vCard export;
- real DOCX/PPTX/XLSX live evaluation fixtures;
- provider-comparison evaluation reports;
- sensitivity-separated retrieval outputs;
- deletion/rebuild controls;
- CI, SBOM, and signed release artifacts;
- Docling or Marker only if lightweight extractors fail on real documents;
- optional semantic retrieval without replacing canonical Markdown.

See [Integrations](docs/integrations.md) for the current capability matrix.

## Documentation

- [Architecture](docs/architecture.md)
- [Installation](docs/installation.md)
- [First-distillation contract](docs/first-distillation-contract.md)
- [Model-provider portability](docs/model-provider-portability.md)
- [Integrations](docs/integrations.md)
- [Egress policy](docs/egress-policy.md)
- [Token-aware research](docs/token-aware-research.md)
- [Threat model](docs/threat-model.md)
- [Migration](docs/migration.md)
- [Clean-room release](docs/clean-room-release.md)
- [Security policy](SECURITY.md)
- [Privacy policy](PRIVACY.md)

## License

Apache License 2.0. See [LICENSE](LICENSE).
