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
| Compound evidence bundles | Supported now | Preserve linked audio, transcripts, typed notes, page renders, and derived text without flattening provenance | Local; review-only manifest with no canonical writes |
| Model egress policy | Supported now | Deny-by-default exact provider/model/purpose/sensitivity authorization and durable decisions | Local gate; an allowed external adapter would still transmit data |
| libmagic + OOXML archive guards | Supported now | Detect actual media type and reject mismatched, encrypted, traversing, oversized, or suspiciously compressed Office packages | Local; `python-magic` is a core dependency |
| PyMuPDF + RapidOCR | Optional now | Native PDF extraction first, then local OCR for scanned pages with page/region anchors and confidence metadata | Local; install with `pip install -e '.[pdf,ocr]'` |
| python-docx | Optional now | DOCX paragraph and table-cell extraction with stable anchors | Local; install with the `office` extra |
| python-pptx | Optional now | PPTX slide text, table cells, and speaker notes with stable anchors | Local; install with the `office` extra |
| openpyxl | Optional now | XLSX sheet/cell extraction with formulas preserved | Local; install with the `office` extra |
| Pillow + RapidOCR | Optional now | Image and business-card text regions with confidence and bounding boxes | Local; install with the `ocr` extra |
| Business-card intake lane | Supported now | Review-only email, explicit-region phone, URL, and anchored OCR field candidates; unclassified text stays unclassified | Local; never infers a current role or auto-creates/merges a contact |
| PDF-deck intake lane | Supported now | Page/slide map with source anchors, extracted text, title candidates, repeated header/footer suppression in the derived view, and vision-review flags | Local; raw extracted text remains authoritative and no whole-deck prompt is constructed |
| Meeting transcript / notes intake | Supported now | Review-only meeting maps from Tactiq-style, Meetily markdown, OpenWhispr-style, or generic timestamped exports, plus typed notes | Local; speaker labels stay opaque, no speaker identity invention, no automatic decisions/owners |
| Local audio transcription helper | Optional now | Explicit-confirm local Faster Whisper transcription into timestamped review-only segments | Local; install with `pip install -e '.[audio]'`; no remote transcription API and no diarization |
| Long-form document map + segment FTS | Supported now | Hierarchical heading/page maps, bounded stable segments, rebuildable per-source segment FTS | Local; whole-document prompts remain forbidden; retrieval returns source anchors only |

PDF extraction uses native text first. Pages without native text are rendered locally and passed to RapidOCR; pages still lacking reliable text are marked `blank-needs-vision`, and an all-unreadable PDF fails closed.

## Deferred document and media adapters

These remain deliberately deferred because the lightweight local adapters above now cover the minimum path:

| Tool | Intended role | Expected data boundary |
|---|---|---|
| Marker or Docling | Richer extraction when RapidOCR quality proves inadequate on representative sources | Local when installed locally; model downloads may require network access |
| Vision verification | Interpret diagrams or low-confidence regions after local OCR | Provider boundary depends on the private deployment; no provider is bundled |

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
- Model egress is now fail-closed and policy-gated, but no provider adapter or automatic Stage 1 caller is included yet.

## Privacy rule for optional tools

Before an optional network tool becomes supported, its documentation and tests must state:

1. what data leaves the machine;
2. where credentials are stored;
3. whether private source text is transmitted;
4. how retries, caching, deletion, and receipts work;
5. whether a local-only mode exists.

No browser profile, credentials, private vault, model transcript, or locally generated index belongs in a public release.