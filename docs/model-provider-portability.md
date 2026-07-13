# Model-provider portability

Constellation does not require the model that built it. It requires a model that can follow the same operating contract.

Changing providers leaves the canonical vault, source hashes, extraction manifests, schemas, search index, sensitivity labels, review candidates, and action ledger unchanged. The variable part is the quality of interpretation during first distillation and later research.

## What instructions can and cannot preserve

Good instructions can preserve:

- the order of operations;
- evidence classes;
- source-claim versus inference separation;
- entity-creation restraint;
- validation and receipt requirements;
- consent and egress boundaries;
- the rule to stop before deeper research.

Instructions cannot make every model equally capable. Models differ in:

- long-context attention;
- conflict detection;
- OCR/vision quality;
- tool-call reliability;
- structured-output compliance;
- tendency to over-summarize or over-infer;
- reasoning over tables, diagrams, and arithmetic;
- latency and cost.

A provider switch therefore needs an evaluation, not faith.

## Why build history is not required

The model does not need a compressed transcript of how Constellation was designed. That history contains false starts and implementation details that may make performance worse.

A fresh provider needs the current contract:

1. what the vault treats as canonical;
2. what tools are deterministic;
3. what the model may read and write;
4. how evidence and inference differ;
5. when first distillation is authorized;
6. what counts as complete;
7. when to stop.

The repository captures this in:

- `README.md` for the human/operator model;
- `docs/first-distillation-contract.md` for the model behavior contract;
- `skills/constellation/SKILL.md` for Hermes prompt injection;
- `docs/egress-policy.md` for provider authorization;
- schemas and validation code for machine-enforced structure.

## Recommended Hermes setup

Use a dedicated Hermes profile for Constellation work. Its startup instructions should require the agent to load the Constellation skill and first-distillation contract before processing an upload.

Start a session with the skill explicitly loaded:

```bash
hermes -p <profile> -s constellation:constellation
```

Select or change the provider through Hermes:

```bash
hermes -p <profile> model
```

Inside an interactive session:

```text
/model
/provider
```

Check the active profile and provider before a live evaluation:

```bash
hermes -p <profile> status
hermes -p <profile> doctor
```

Do not store API keys in a vault, skill, README, receipt, or scheduled prompt. Provider credentials belong in Hermes configuration, environment files, or credential pools.

## Prompt placement order

From strongest and most reliable to weakest:

1. Machine-enforced schema, path, hash, egress, and validation checks.
2. Profile startup instructions (`AGENTS.md` or equivalent).
3. Explicitly loaded Constellation skill.
4. First-distillation contract included in the task context.
5. Source-specific request and bounded evidence packet.
6. Conversation history.

Do not rely on conversation history for core behavior. It is the first context discarded during compression and the least portable layer across providers.

A Unix man page can help a human remember commands, but it is not automatically placed in the model context. For model efficacy, a Hermes skill plus profile startup instructions is the better mechanism.

## Provider evaluation set

Before changing the unattended provider, use a small fixed set that covers different failure modes. Synthetic or consented private fixtures should include:

1. A native-text PDF with dates, claims, and a numerical inconsistency.
2. A scanned PDF with one weak OCR page.
3. A business card with a brand and a legally distinct company name.
4. A slide deck where diagrams carry meaning not present in linear text.
5. A spreadsheet with formulas, labels, and blank cells.
6. A source that conflicts with an existing canonical entity note.
7. A low-value source where the correct action is not to create entities.

Keep the original fixtures fixed. Provider comparisons are meaningless when each model sees different sources or different vault context.

## Evaluation procedure

For each provider/model pair:

1. Start a fresh session.
2. Load the same Constellation skill and contract.
3. Use the same extraction manifest and text.
4. Retrieve the same bounded canonical context.
5. Allow the same tools and turn limit.
6. Record the provider, model, reasoning level, latency, and token usage.
7. Diff the proposed or canonical notes.
8. Score the result using the rubric in `first-distillation-contract.md`.
9. Check validation, index freshness, and receipt completeness.
10. Review privacy and unauthorized-research behavior separately from prose quality.

Recommended acceptance rule:

- average at least 8/10 across representative sources;
- no provenance failures;
- no sensitivity or egress violations;
- no unauthorized web research;
- no invented identity merge;
- no missing receipt or validation step.

A cheaper model that scores slightly lower may be appropriate for mechanical triage. A stronger model may remain necessary for ambiguous entity conflicts, visual decks, contracts, or high-stakes strategy material.

## Model roles instead of one universal model

Provider independence does not require one model to handle every stage.

A practical deployment can use:

- no model for preservation, hashing, extraction, validation, and indexing;
- a reliable mid-cost model for ordinary first distillation;
- a vision-capable model only when image/layout interpretation is necessary;
- a stronger reasoning model for explicitly requested cross-source synthesis;
- local models for sensitive sources when egress is not authorized.

Each transition must remain explicit in the receipt or research ledger.

## Vision capability

A text-only model can distill native extraction but cannot reliably recover visual meaning from:

- business-card layout;
- charts;
- diagrams;
- slide composition;
- handwriting;
- screenshots with OCR errors.

The deployment should detect whether the selected provider supports vision. If it does not, use local OCR and mark unresolved visual interpretation as `partial`, or route only the necessary image to a separately authorized vision provider. Never pretend that linear OCR captured spatial meaning it did not capture.

## Egress policy when switching

A provider change is also a data-routing change.

Before sending a live source to a new provider:

- confirm the provider and exact model are allowed;
- confirm the task purpose is allowed;
- confirm the source sensitivity is allowed;
- record the decision;
- verify that fallback routing cannot silently select an unapproved provider.

The fact that a provider is configured in Hermes does not mean Constellation has authorized it for every source.

## Scheduled and unattended jobs

Jobs run in fresh sessions. Their prompts must be self-contained and include or load:

- the vault path;
- the Constellation skill;
- the first-distillation contract;
- the allowed source/event input;
- the consent boundary;
- the exact stop condition;
- receipt and notification requirements;
- the provider/model or a policy that chooses one safely.

Do not write "process new files as usual" and depend on historical chat context.

## Rollback plan

Keep the previous provider configuration until the new one passes evaluation.

If quality regresses:

1. Stop unattended intake or disable its model pass.
2. Keep deterministic ingestion active if safe.
3. Restore the previous provider/model.
4. Re-run failed sources from preserved originals into review candidates.
5. Compare outputs and record the failure pattern.
6. Improve the contract only when the issue is procedural; switch models when the issue is capability.

The preserved source and extraction manifest make re-distillation possible without re-uploading the document.

## Symptoms of a weak provider fit

- generic summaries without exact claims or dates;
- every proper noun becomes an entity;
- old evidence is overwritten rather than preserved as a conflict;
- document claims are written as verified facts;
- arithmetic is repeated without checking internal consistency;
- the model ignores sensitivity labels;
- it browses the web without authorization;
- it writes long essays for simple sources;
- it omits validation, indexing, or the receipt;
- it cannot stop after the bounded pass.

These are provider-evaluation failures, not reasons to weaken the canonical rules.

## Minimum portable instruction

When integration space is limited, include this compact instruction and link the full contract:

```text
Operate Constellation as a source-grounded vault. Preserve mechanical facts,
source claims, prior-vault evidence, and inference as separate layers. Use only
the bounded evidence packet and canonical context supplied. Preserve conflicts;
do not invent entity matches. A deliberate upload authorizes one first
distillation only, not web research or repeated passes. Validate every canonical
write, verify index freshness, write a completion/partial/failure receipt, and
stop. Follow docs/first-distillation-contract.md.
```

The full contract should still be available to the model whenever possible.
