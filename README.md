# Hermes Constellation

**Turn documents, research, and relationships into an AI-maintained knowledge base that becomes more useful with every source.**

[Download v0.1.0](https://github.com/skyeyesec333/hermes-constellation/releases/tag/v0.1.0) · [Installation](docs/installation.md) · [Architecture](docs/architecture.md) · [Hermes Agent](https://github.com/NousResearch/hermes-agent)

Constellation is a local-first knowledge and relationship workspace for Hermes Agent. It preserves original files, builds a source-grounded Markdown wiki around them, and gives agents bounded tools to ingest, search, validate, and review that knowledge.

Your files and Markdown remain the record. Obsidian can open the vault directly. SQLite indexes, OCR text, generated catalogs, and model output can be rebuilt or replaced.

Version 0.1.0 is the first public clean-room release. It contains no private vault, contacts, reports, credentials, browser state, model transcripts, or host-specific automation.

## Why I built this

Constellation began with a practical need: I wanted a private Obsidian workspace that could also function as a relationship CRM and a long-lived research base.

I wanted to be able to drop in a business card, slide deck, paper, spreadsheet, screenshot, or PDF and have an agent do the tedious work: preserve it, read it, identify what matters, connect it to people and organizations already in the vault, flag conflicts, and file the result somewhere useful. I also wanted research agents to revisit that material later and build better syntheses instead of starting from zero in every chat.

The original inspiration was [Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). Its central idea is simple and powerful: do not use an LLM only to retrieve raw chunks at question time. Let the agent incrementally maintain a persistent, interlinked Markdown wiki. The knowledge is compiled once, corrected over time, and enriched as new sources arrive.

Constellation takes that pattern in a more operational direction. A useful personal or business knowledge base needs more than good summaries. It needs to remember:

- where a statement came from;
- whether it is a source claim, verified fact, or inference;
- which person, company, project, or relationship it concerns;
- what changed when newer evidence arrived;
- which edits were mechanical and which changed meaning;
- what an agent was allowed to send to an external model;
- what was reviewed, rejected, or left unresolved.

That is why Constellation keeps sources and interpretation separate. It combines the compounding LLM wiki idea with provenance, review gates, conflict protection, privacy controls, and a relationship-oriented record model.

The goal is not to collect the largest pile of notes. It is to build knowledge that can still be trusted after hundreds of sources and many different model sessions.

## What using it feels like

```text
Drop in a file
  -> preserve the original bytes
  -> detect the real file type
  -> calculate SHA-256 provenance
  -> extract text or OCR locally
  -> record page/slide/cell/region anchors
  -> register or stage a source record
  -> validate canonical Markdown
  -> rebuild local search
  -> optionally run one bounded first distillation
  -> propose connected entities, claims, and questions
  -> review meaning-bearing changes
```

A business card can become a preserved image, OCR regions, a source record, and a reviewed person/company update. A slide deck can become page-aware evidence linked to the companies, projects, and claims it discusses. A research paper can update an existing topic while preserving the old view and showing what changed.

The deterministic path does not require an LLM. Model interpretation and external research are separate, explicit layers.

## Try it

Requirements:

- Python 3.11 or newer;
- `libmagic` available to `python-magic`;
- SQLite with FTS5 support.

Clone the repository and install the document adapters:

```bash
git clone https://github.com/skyeyesec333/hermes-constellation.git
cd hermes-constellation
python3 -m venv .venv
.venv/bin/pip install -e '.[pdf,office,ocr]'
```

Create a vault and ingest the fictional demo source:

```bash
CONSTELLATION=./.venv/bin/constellation
VAULT="$HOME/my-constellation"

$CONSTELLATION init "$VAULT"
$CONSTELLATION doctor "$VAULT"

cp examples/synthetic-demo-vault/Inbox/Files/demo-brief.txt \
  "$VAULT/Inbox/Files/demo-brief.txt"

$CONSTELLATION ingest "$VAULT" "$VAULT/Inbox/Files/demo-brief.txt"
$CONSTELLATION review "$VAULT" list
```

New vaults stage a create-only source candidate by default. Review the exact candidate, then promote it:

```bash
$CONSTELLATION review "$VAULT" promote \
  --candidate <candidate-id> \
  --confirm

$CONSTELLATION validate "$VAULT"
$CONSTELLATION search "$VAULT" "Northstar Field Labs"
```

The result is a normal folder of source files and Markdown. Open it in Obsidian, inspect it with any text editor, version a sanitized copy with Git, or operate it entirely from the CLI.

For wheel and Hermes-specific installation, see [Installation](docs/installation.md).

## What ships in v0.1

### A canonical local vault

- Versioned Markdown schemas for source items, entities, claims, research runs, and candidate patches.
- Original source preservation inside the vault.
- SHA-256 provenance and immutable extraction manifests.
- Atomic, path-contained writes.
- Expected-base-hash checks that stop stale updates from overwriting newer notes.
- A generated human-readable `INDEX.md`.
- Rebuildable SQLite FTS5 search over canonical records.

### Local document intake

| Input | Local adapter | Evidence anchors |
|---|---|---|
| Text and Markdown | UTF-8 reader | line ranges |
| Native or scanned PDF | PyMuPDF, with RapidOCR where needed | page and OCR region |
| DOCX | python-docx | paragraph and table cell |
| PPTX | python-pptx, with MarkItDown fallback | slide text, table cell, speaker notes |
| XLSX | openpyxl | sheet and cell |
| PNG, JPEG, WebP, TIFF, BMP | Pillow and RapidOCR | OCR region and bounding box |

Constellation reads native PDF text first. It runs OCR only on pages that need it. OCR results include confidence, bounding boxes, dimensions, warnings, and partial/failure status. The original source remains the final reference.

### Evidence and intake safety

- MIME detection with `libmagic` instead of trusting only the extension.
- Internal OOXML checks for DOCX, PPTX, and XLSX.
- Limits on source size, expanded archive size, archive entries, compression ratio, unsafe paths, and encrypted packages.
- Stable anchors such as `P0007`, `SLIDE0005:NOTES`, `SHEET0002:B17`, and `OCR:R0008`.
- Extraction manifests that record engines, versions, hashes, warnings, and status.

### Review, retrieval, and privacy

- Automatic registration only for mechanical source records when the vault policy allows it.
- Create-only candidates when automatic registration is disabled.
- Explicit promotion for claims, entities, merges, and other meaning-bearing changes.
- Action-ledger entries for promoted changes.
- Exact-ID and FTS5 search with sensitivity ceilings.
- Evidence packets containing record ID, path, route, sensitivity, score, and anchor.
- Deny-by-default provider/model/purpose/sensitivity egress policy.
- Research receipts with provider, model, token accounting, evidence hashes, retries, budget, and completion status.

### Clean public release tooling

- An explicit file-lineage allowlist.
- A one-way clean-room release compiler.
- Privacy scanning for paths, secrets, hostnames, usernames, and private canaries.
- A fictional `example.test` demo vault.

## The knowledge model

A Constellation vault has four layers.

### 1. Preserved sources

Original files are immutable evidence. A source is never replaced by its summary.

### 2. Canonical Markdown

Canonical records live in:

```text
source-items/   what entered the vault and where it came from
entities/       people, organizations, products, places, and other subjects
claims/         important assertions tied to evidence
research/       bounded research runs and their receipts
```

Candidates are proposals. They are not canonical evidence until promoted.

### 3. Rebuildable retrieval

SQLite FTS, generated indexes, extracted text, and optional semantic indexes help agents find material. They are disposable infrastructure, not the source of truth.

### 4. Model synthesis

A model can summarize, connect, compare, find contradictions, and propose updates. The model does not get to erase provenance or silently promote its interpretation into fact.

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
├── Inbox/Files/
├── Library/Files/
├── Library/Text/
├── source-items/
├── entities/
├── claims/
├── research/
├── HOME.md
└── INDEX.md
```

## Hermes plugin

The package includes a Hermes Agent plugin and a provider-independent operating skill.

The plugin registers five bounded tools:

| Tool | Purpose |
|---|---|
| `constellation_status` | Inspect capabilities and vault health |
| `constellation_ingest` | Preserve and extract a local source |
| `constellation_validate` | Validate canonical records and index state |
| `constellation_search` | Retrieve source-grounded evidence packets |
| `constellation_review` | List or promote an exact candidate |

It also provides:

- `hermes constellation ...`;
- `/constellation ...` inside a Hermes session;
- the loadable skill `constellation:constellation`.

Install the package into the same Python environment that runs Hermes, then enable it:

```bash
python -m pip install -e '.[pdf,office,ocr]'
hermes plugins enable constellation
hermes plugins list
hermes doctor
```

Load the operating skill explicitly when needed:

```bash
hermes -s constellation:constellation
```

The skill tells the active model how to preserve evidence, separate claims from inference, avoid speculative entity matches, validate writes, respect egress policy, and stop before unauthorized research.

## The tools Constellation is designed to work with

The public package supplies the trusted core. A full deployment becomes much more useful when a Hermes agent orchestrates other tools around it.

Constellation does **not** silently call these tools. The active Hermes profile or an explicit workflow chooses them, and networked or meaning-bearing actions should require permission.

### Collection and intake

- **Google Workspace / Drive** for watched or manually selected intake folders.
- **Obsidian Web Clipper** for saving articles as local Markdown.
- **QuickAdd and Templater** for structured manual capture inside Obsidian.
- **Hermes messaging gateways** for deliberate file handoff and completion notices.

Drive polling, messaging delivery, and background scheduling are deployment adapters. They are not bundled in v0.1.

### Documents, OCR, and vision

- The bundled PyMuPDF, RapidOCR, python-docx, python-pptx, openpyxl, Pillow, and MarkItDown adapters handle the deterministic first pass.
- Hermes document/OCR tooling can convert additional formats before ingestion.
- A vision-capable model can inspect diagrams, charts, business-card layout, handwriting, or low-confidence regions when the vault's egress policy allows it.
- Docling, Marker, OCRmyPDF, or Tesseract can be added as specialist fallbacks after representative tests justify the extra complexity.

Linear OCR is not visual understanding. If layout carries meaning and no authorized vision path is available, the correct result is `partial`, not a confident guess.

### Web and research

A research-enabled Hermes profile can use:

- `web_search` and `web_extract` for ordinary discovery and extraction;
- self-hosted SearXNG for broad search discovery;
- self-hosted or external Firecrawl for clean page extraction;
- Hermes browser tools or Camofox for dynamic pages;
- `linkedin-osint` for consented person and relationship research;
- `osint-lv2` for deeper business-professional research;
- `osint-refine` to absorb operator-supplied intelligence, corroborate it, and refine an existing dossier;
- general OSINT, SEC EDGAR, and arXiv skills for domain-specific evidence;
- OpenAlex, Crossref, Semantic Scholar, Unpaywall, Open Library, or Zotero adapters for papers and books;
- X/Twitter and YouTube transcript tools when those sources are relevant.

These are follow-up research paths, not automatic consequences of uploading a file. A source can be safely ingested without authorizing LinkedIn research, broad crawling, or repeated model calls.

### Retrieval and synthesis

- Built-in SQLite FTS5 is the supported lexical index.
- A local Chroma/Ollama embedding index or a tool such as `qmd` can add semantic retrieval, but it should remain rebuildable and sensitivity-filtered.
- The Constellation operating skill governs ordinary first distillation.
- A stronger reasoning model can be reserved for explicitly requested cross-source synthesis, difficult conflicts, contracts, or strategic analysis.
- A vision model should be used only for the pages or regions that require visual reasoning.

Changing models does not change the evidence substrate. See [Model-provider portability](docs/model-provider-portability.md).

### Human-facing Obsidian layer

Obsidian is the interface, not the database engine. A useful companion setup is:

- **Hermes Console** to work with the agent from inside Obsidian;
- **Dataview or Bases** for relationship, source, and status views;
- **Kanban** for opportunity or lead stages;
- **Tasks** for dated follow-ups;
- **QuickAdd and Templater** for consistent capture;
- **Excalidraw** for diagrams when useful.

These plugins render or edit Markdown. They are optional and are not included in the Python package. Smart Connections can provide related-note UX, but it creates a separate embedding store and is not required for Constellation retrieval.

## Recommended agent workflows

### Ingest

1. Preserve the source and extract local evidence.
2. Inspect extraction warnings and blank units.
3. Register or review the mechanical source record.
4. Run one bounded first distillation only when the deployment has an explicit consent signal.
5. Propose clearly supported entities, claims, conflicts, and open questions.
6. Validate, rebuild search, write a receipt, and stop.

### Query

1. Search canonical records first.
2. Return evidence packets with paths and anchors.
3. Read only the relevant records and source passages.
4. Synthesize an answer with source boundaries intact.
5. File a useful new synthesis back into the vault only when requested or allowed.

### Research

1. Start with a specific question, scope, evidence need, budget, and stop condition.
2. Search primary and domain-specific sources before broad crawling.
3. Separate collection, claim checking, synthesis, evaluation, and promotion.
4. Record provider/model use and evidence hashes in a receipt.
5. Return `partial` when the evidence or budget is insufficient.

### Lint and maintenance

- find stale claims and unresolved conflicts;
- detect orphaned or duplicate records;
- identify missing cross-references;
- rebuild indexes from canonical Markdown;
- keep candidates and generated artifacts out of canonical search;
- propose new research questions without launching research automatically.

## First distillation is deliberately bounded

A deliberate upload may authorize one interpretation pass if the deployment explicitly defines that policy. The pass can:

- identify what the source is;
- summarize it without replacing it;
- list important source-backed facts;
- identify clearly named entities;
- connect it to a small set of existing records;
- separate mechanical facts, source claims, prior evidence, and inference;
- preserve contradictions;
- classify relevance and sensitivity;
- leave open questions and follow-up options;
- write a completion receipt and stop.

It does not automatically authorize broad web research, LinkedIn OSINT, company deep-dives, outreach, publishing, speculative entity creation, or repeated synthesis passes.

Read the full [First-distillation contract](docs/first-distillation-contract.md).

## Privacy and model choice

Local preservation, hashing, parsing, OCR, validation, and FTS indexing do not require a model provider.

Model egress fails closed unless vault policy authorizes the exact provider, transport, model, purpose, and maximum sensitivity. Each decision is written to an egress ledger without storing source text or credentials there.

A configured model is not automatically approved for every source. A provider switch is also a data-routing change and should be evaluated with fixed representative sources.

Constellation is designed so that:

```text
no model                    preservation, hashing, extraction, validation, indexing
ordinary reasoning model    routine first distillation
vision model                layouts, charts, cards, diagrams, difficult OCR
stronger reasoning model    requested cross-source synthesis and hard conflicts
```

Instructions improve consistency, but they cannot make every model equally capable. The repository includes a Hermes skill, a first-distillation contract, an egress policy, and a provider evaluation procedure so model changes can be tested instead of guessed.

Read [Model-provider portability](docs/model-provider-portability.md), [Egress policy](docs/egress-policy.md), [Threat model](docs/threat-model.md), and [Privacy](PRIVACY.md) before connecting live private sources to an external provider.

## What v0.1 does not pretend to be

The public package is a trustworthy core, not a finished autonomous research product or hosted CRM.

It does not bundle:

- Google Drive polling or Telegram delivery;
- an LLM provider or automatic model invocation;
- automatic web or LinkedIn research;
- automatic entity resolution;
- a finished relationship dashboard or lead pipeline;
- a hosted sync service;
- vector or graph infrastructure;
- audio/video ingestion;
- private prompts, notes, contacts, or automation.

Those can be added around the core, but they must preserve the same provenance, consent, sensitivity, and review boundaries.

## Obsidian, Git, and ownership

A Constellation vault is a folder of ordinary files. Obsidian can open it directly, and the agent can maintain the same files through Hermes.

Keep the human surface narrow: `HOME.md`, active relationships, current opportunities, intake, and recent work. Let agents use the wider entity/source tree and generated index. The machine-friendly catalog does not need to be the human home page.

Git can version source code and a deliberately sanitized vault. Do not put a live private vault, credentials, browser state, generated model transcripts, or sensitive attachments into a public repository.

If a Linux server owns the canonical vault and macOS mounts it through SSHFS, open the mounted vault directly in Obsidian. Do not run another two-way sync system against the same agent-written mount.

## Known limits

- First distillation is only as good as the selected model and bounded context.
- OCR can miss handwriting, stylized text, low-resolution scans, and diagrams.
- Native slide and PDF text may lose spatial relationships.
- Automatic entity matching is not implemented.
- SQLite FTS5 is lexical, not semantic retrieval.
- Encrypted Office files are rejected rather than decrypted.
- Network research, Drive, messaging, and scheduling require deployment adapters.
- v0.1 has no audio/video intake.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,pdf,office,ocr]'
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python -m build
```

The test suite covers schemas, path containment, ingestion, OCR boundaries, Office archive safety, review promotion, stale-index failure, research receipts, egress policy, packaging, plugin discovery, migration rehearsal, and clean-room release behavior.

## Documentation

- [Installation](docs/installation.md)
- [Architecture](docs/architecture.md)
- [First-distillation contract](docs/first-distillation-contract.md)
- [Model-provider portability](docs/model-provider-portability.md)
- [Integrations and tool status](docs/integrations.md)
- [Egress policy](docs/egress-policy.md)
- [Token-aware research](docs/token-aware-research.md)
- [Threat model](docs/threat-model.md)
- [Migration](docs/migration.md)
- [Clean-room release](docs/clean-room-release.md)
- [Security policy](SECURITY.md)
- [Privacy policy](PRIVACY.md)

## License

Apache License 2.0. See [LICENSE](LICENSE).
