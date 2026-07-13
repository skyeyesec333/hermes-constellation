# Integrations and Tool Status

Constellation uses three status labels:

- **Supported now**: implemented and covered by the current test suite.
- **Optional now**: implemented, but installed separately.
- **Planned**: named for transparency; no working Constellation adapter is shipped yet.

A planned tool is not a promise that it is bundled or safe to use with private data.

## Supported now

| Tool or capability | Status | Purpose | Data boundary |
|---|---|---|---|
| Hermes Agent plugin | Supported now | Five bounded tools, `hermes constellation`, `/constellation`, and the Constellation skill | Local unless the caller separately invokes a network provider |
| Python, Pydantic, and PyYAML | Supported now | Strict records, configuration, validation, manifests, and receipts | Local |
| Markdown and UTF-8 text intake | Supported now | Preserve original bytes, extract text, stage a review candidate, and record hashes/line anchors | Local |
| SQLite FTS5 | Supported now | Rebuildable canonical-only full-text retrieval | Local; SQLite is part of Python on supported builds |
| Research receipt ledger | Supported now | Budget enforcement and accountable provider/model/token/evidence records | Local accounting only; it does not call a model provider |
| PyMuPDF | Optional now | Native-text PDF extraction with page anchors and blank-page detection | Local; install with `pip install -e '.[pdf]'` for development |

Native-text PDF support is not OCR. A scanned or image-only PDF is rejected instead of being recorded as successfully extracted.

## Planned document and media adapters

These are not implemented in this release:

| Tool | Intended role | Expected data boundary |
|---|---|---|
| OCRmyPDF and Tesseract | OCR for scanned PDFs while retaining page structure | Local when installed locally |
| Docling | Richer extraction from PDFs, Office documents, tables, and layout | Local when installed locally; model downloads may require network access |
| Image/screenshot analysis | Extract text and visual evidence from screenshots, charts, and diagrams | Not selected; boundary depends on the future vision provider |
| DOCX/PPTX adapters | Preserve originals and extract paragraphs, tables, slides, and anchors | Local adapter planned |

## Planned web and research adapters

These are also not implemented in this release:

| Tool | Intended role | Expected data boundary |
|---|---|---|
| Hermes Camofox/browser tools | Dynamic website capture after ordinary extraction fails | Sends requests to visited websites; browser state must stay outside public artifacts |
| Firecrawl | Web-page extraction | Self-hosted or external depending on deployment |
| SearXNG | Search discovery | Self-hosted or external depending on deployment |
| OpenAlex, Crossref, Semantic Scholar | Scholarly metadata and source discovery | Sends search identifiers/queries to external APIs |

## Planned reasoning and retrieval adapters

- Automatic Stage 1 overview, synthesis, and entity/concept/strategy staging is not active.
- No model provider is bundled or selected by the public package.
- Optional semantic/vector retrieval is not included; SQLite FTS5 remains the implemented index.
- Sending private source text to an external model requires a future sensitivity/egress policy. The current receipt ledger can record calls but is not that policy.

## Privacy rule for optional tools

Before an optional network tool becomes supported, its documentation and tests must state:

1. what data leaves the machine;
2. where credentials are stored;
3. whether private source text is transmitted;
4. how retries, caching, deletion, and receipts work;
5. whether a local-only mode exists.

No browser profile, credentials, private vault, model transcript, or locally generated index belongs in a public release.