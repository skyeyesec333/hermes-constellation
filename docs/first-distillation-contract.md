# First-distillation contract

This document defines the recommended model pass that may follow deterministic Constellation ingestion. The public v0.1 package does not call an LLM automatically. A Hermes profile, Drive watcher, messaging adapter, or other deployment can implement this contract around the core.

The contract is model-neutral. A provider may change, but the evidence classes, write boundaries, and completion requirements should not.

## Authorization boundary

A first distillation is allowed only when the deployment has a clear consent signal. Examples:

- the user deliberately places a file in a designated Constellation intake folder;
- the user attaches a source and explicitly asks an agent to ingest it;
- an approved API client submits a source with `first_distillation=true`.

The following are not consent signals:

- a routine index rebuild;
- a maintenance timer;
- a file already present in the vault;
- a search query;
- a queue replay after state loss;
- a model deciding that more research would be useful.

Consent authorizes one bounded pass over the source and relevant local context. It does not authorize broad web research, LinkedIn lookup, OSINT, contacting people, publishing, or repeated autonomous passes.

## Required input packet

The model should receive a bounded packet rather than an unrestricted vault dump:

- source ID;
- source title and media type;
- SHA-256;
- vault-relative original path;
- extraction status and warnings;
- extracted text with stable evidence anchors;
- OCR confidence and bounding boxes when relevant;
- sensitivity classification or default;
- a small set of retrieved canonical records that may provide context;
- the current first-distillation contract;
- exact write and receipt paths allowed by the deployment.

If extraction failed or is too weak to support a useful pass, stop with `partial` or `failed`. Do not fill missing evidence with plausible prose.

## Evidence classes

Keep these layers separate in the note:

### Verified mechanical facts

Facts produced by deterministic local tooling:

- file hash;
- file size;
- detected media type;
- page or slide count;
- extraction engine and version;
- extraction status;
- OCR confidence;
- preserved and extracted-text paths.

### Source claims

What the document, image, or author says. A statement printed in a deck is a source claim even when it looks numerical or authoritative.

Examples:

- a company claims a deployment is compliant;
- a business card prints a job title;
- a proposal states expected savings;
- a report cites a market statistic.

Do not silently promote these to independently verified facts.

### Prior-vault evidence

Claims already present in canonical records. Include their paths or evidence anchors. Prior notes are context, not proof that the current source agrees with them.

### Inference

The model's interpretation, relevance judgment, or proposed connection. Label it. A reader should be able to remove the inference section and still retain an accurate account of the source.

## Required procedure

### 1. Confirm source identity

Verify that the source ID, hash, original path, extraction manifest, and extracted-text path agree. If they do not, stop. Never distill content under the identity of another file.

### 2. Read the extraction quality first

Check:

- status (`complete`, `partial`, or `failed`);
- blank or failed units;
- warnings;
- OCR confidence;
- whether the text came from native extraction, OCR, or vision;
- whether diagrams or layout may carry meaning missing from linear text.

Use vision only when the deployment authorizes it and local extraction is incomplete or spatial interpretation matters. Record that a vision pass occurred.

### 3. Identify the document's job

State what the source is before interpreting it. Examples:

- business card;
- buyer-facing pitch deck;
- board memo;
- contract draft;
- research excerpt;
- spreadsheet model;
- secondary timeline graphic;
- screenshot of an unverified claim.

Also state what it is not when that prevents misuse. An old European market report is not current ASEAN intelligence. A sales deck is not certification evidence. A business card is not proof of current employment.

### 4. Produce a concise first summary

Summarize the source's purpose and most consequential content. Do not replace the source with a long paraphrase. Put detailed extraction or page-by-page notes in a separate derived note only when needed.

### 5. Extract important source-backed facts

Use stable anchors where practical. Preserve qualifiers, dates, ranges, units, and attribution. Do not repair a source's arithmetic or chronology silently. If a contradiction is visible, quote both sides and flag it.

### 6. Resolve context conservatively

Retrieve only a small number of clearly relevant canonical records. Use them to:

- link an existing person or organization;
- recognize that the source repeats known material;
- identify a conflict;
- avoid creating a duplicate entity;
- understand why the source may matter.

Do not force every source into an existing story.

### 7. Apply entity restraint

Create or propose an entity only when identity is clear and the entity will be useful in future retrieval.

Good candidates:

- the named person on a business card;
- the legal organization named on a contract;
- the buyer and seller in a proposal;
- a product that recurs across sources.

Usually skip:

- byline authors who are peripheral to the user's work;
- every city mentioned in a report;
- logos without clear identity;
- ambiguous names;
- one-off competitors listed for comparison;
- an inferred parent/subsidiary relationship not stated in evidence.

Keep brands, legal entities, products, and people distinct until evidence supports a merge.

### 8. Preserve conflicts

When the source conflicts with stronger or earlier evidence:

- record both versions;
- identify each evidence source;
- state which version currently appears stronger and why;
- do not delete the weaker claim;
- add a follow-up question if resolution matters.

### 9. Classify relevance and sensitivity

Explain relevance in plain terms. Do not inflate every source to `high` relevance.

Use the vault's sensitivity policy. Personal contact details, contracts, pricing, private correspondence, and internal strategy normally require stronger handling than public reports.

### 10. Write bounded outputs

A normal first pass may write:

- one canonical source-item;
- extracted text or an appended vision correction;
- a small number of clear entity records or conflict-safe updates;
- one derived analysis note when the source genuinely warrants it;
- one completion receipt.

It should not create a large graph from a single weak source.

### 11. Validate and index

After canonical writes:

- validate all affected records;
- rebuild or verify the canonical index;
- confirm that the source is retrievable by a distinctive name or phrase;
- fail closed if validation or index freshness fails.

### 12. Write the receipt and stop

A successful receipt should include:

```json
{
  "schema_version": "0.1",
  "kind": "constellation-first-distillation-receipt",
  "status": "complete",
  "source_sha256": "...",
  "source_id": "...",
  "original_path": "...",
  "completed_at": "...",
  "summary": "...",
  "notes_written": ["..."],
  "follow_up_options": ["..."]
}
```

Use `partial` when useful work was preserved but an extraction, validation, or context requirement remains unresolved. Use `failed` when no reliable canonical result was produced.

The pass ends after the receipt. Follow-up options are suggestions, not queued work.

## Recommended source-item structure

```markdown
# Human-readable source title

## What this source is

Document type, purpose, provenance, and important scope limits.

## First distillation summary

Short account of the document's purpose and consequential content.

## Key facts

Source-backed claims with dates, units, qualifiers, and evidence anchors.

## Verified mechanical facts

Hash, format, extraction status, engine, and preserved paths.

## Source claims vs inference

Explicit separation of what the source says, what prior evidence says, and what the model infers.

## Entities named

Existing links, newly proposed entities, and intentionally skipped peripheral names.

## Relevance

Why this matters, how much, and what it does not prove.

## Conflicts and caveats

Contradictions, stale claims, arithmetic issues, OCR uncertainty, or missing visual context.

## Open questions

Questions that require the user's context or an explicitly authorized deeper pass.

## Evidence references

Original, extracted text, manifest, related canonical notes, and exact anchors.
```

Not every source needs every heading. A business card should not become a 15-page strategy memo. A complex confidential deck may justify more detail.

## Quality rubric

Score a provider on the same source set before adopting it.

### Evidence fidelity: 0-2

- 0: invents or materially changes source facts.
- 1: mostly accurate but drops qualifiers, dates, or attribution.
- 2: accurately preserves claims, units, attribution, and anchors.

### Claim/inference separation: 0-2

- 0: presents inference as fact.
- 1: labels some interpretation but still blurs layers.
- 2: keeps mechanical facts, source claims, prior evidence, and inference distinct.

### Conflict handling: 0-2

- 0: silently chooses or overwrites one version.
- 1: notices the conflict but does not preserve its evidence.
- 2: records both versions, evidence, and current confidence.

### Entity restraint: 0-2

- 0: creates duplicates or speculative relationships.
- 1: mostly restrained with a few unnecessary nodes.
- 2: reuses clear identities and skips peripheral or ambiguous entities.

### Operational compliance: 0-2

- 0: violates write, sensitivity, research, or egress boundaries.
- 1: produces useful output but misses a receipt, validation, or stop condition.
- 2: validates, indexes, writes a complete receipt, and stops.

A model should average at least 8/10 on representative sources before becoming the unattended first-distillation provider. Any privacy, provenance, or unsupported-research violation is a hard failure regardless of score.

## Common failure patterns

### Generic summary

The output could describe any document in the same category. Fix by requiring exact names, dates, claims, units, and evidence anchors.

### Entity explosion

The model creates a node for every proper noun. Fix by enforcing usefulness and identity clarity.

### Helpful hallucination

The model fills an unreadable field or missing context with a plausible answer. Fix by requiring `partial` status and an open question.

### Source laundering

A deck claim becomes a canonical fact because it was repeated confidently. Fix by labeling source claims and preserving attribution.

### Context capture

The source is forced into an existing deal or thesis without evidence that the upload relates to it. Fix by labeling contextual relevance as inference and asking why the source was uploaded.

### Unbounded research

The model starts browsing because enrichment seems useful. Fix by ending after the receipt and requiring a separate explicit research request.

### Over-distillation

A simple source becomes a long essay. Fix by making the source-item concise and creating a separate derived note only when the source warrants it.

## Provider independence

The contract should be placed where every provider receives it:

- in a Hermes skill loaded for Constellation tasks;
- in the profile's startup instructions;
- in a scheduled job prompt if the job invokes first distillation;
- in the deployment's system prompt or policy layer for non-Hermes clients.

Do not depend on conversation history. A fresh model session should be able to perform a compliant first pass using only this contract, the source packet, and bounded canonical context.
