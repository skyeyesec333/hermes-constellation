# Constellation implementation roadmap

This file is the durable cross-model handoff. Conversation history is not required.

## Active stage

**Stage 1 — release integrity and v0.2 candidate**

Status: `blocked — waiting for explicit private acceptance/public-promotion approval`

Immediate plan: `docs/plans/01-release-integrity.md`

Stage 0 completed on 2026-07-17 with these executed gates:

- `scripts/verify_fast.sh`: passed Ruff and all 189 tests.
- `scripts/verify_release.sh`: built the wheel, compiled and audited the complete allowlisted public tree, installed the wheel in a fresh virtual environment, initialized and validated a fresh vault, and passed. The script prints the current file count and tree SHA-256 on every run; do not rely on a hash embedded in this file because this file is itself part of that tree.
- Project-specific Kilo agent frontmatter was parsed and validated locally. Kilo itself is not installed on this host; Kilo's official instruction contract identifies root `AGENTS.md` and `.kilo/agents/*.md` as supported project context.
- Hermes project `constellation-public` is bound to the dedicated manual `constellation-public` Kanban board.

Do not start Task 1.1 until Bryan explicitly approves public promotion after the corresponding private CSO behavior has worked for him and Aiko. Re-run the gates rather than trusting historical results after any change.

## What is already established

- The upgraded development line contains the richer canonical record model, migration tooling, research adapters, provider portability, review-only promotion, egress policy, release compiler, and a 186-test suite.
- The upgraded line and public `origin/main` have unrelated Git roots. They cannot be safely reconciled through a normal merge.
- The public release process is intentionally one-way and allowlist-driven.
- The current README is a short product pitch, not sufficient installation or onboarding documentation.
- The optional network research runner and its escalation adapters do not yet have adequate direct test coverage.
- Vault health and retrieval completeness need stricter semantics: invalid canonical material must not coexist with a green health result or an apparently complete index.
- The private CSO constellation is the upstream implementation and dogfooding lane. Bryan and Aiko test changes there first. Public development begins only after explicit approval and uses synthetic fixtures plus a generalized behavior contract.

## Operating model

- Primary implementation and acceptance: the private CSO constellation through Bryan's conversations and Aiko's workflow, usually with DeepSeek v4 through Kilo Code.
- Downstream public promotion: the dedicated `constellationdeveloper` Hermes profile or the project Kilo agent, only after explicit approval.
- Scarce Codex/Terra/Sol use: architecture decisions, difficult correctness problems, release-boundary review, and final security review.
- One active implementation or promotion task at a time.
- No automatic task decomposition or agent swarms.
- Independent review only for security-sensitive work or a stage-closing change.
- Git and deterministic scripts are authoritative; Kanban records local execution state.

## Stage 0 — Development continuity foundation

Deliver:

- provider-neutral `AGENTS.md`;
- Kilo maintainer and read-only reviewer definitions;
- this roadmap and the first executable implementation plan;
- `CONTRIBUTING.md` and `SECURITY.md`;
- fast and release verification entrypoints;
- baseline GitHub Actions CI;
- manual Hermes Project/Kanban wiring.

Exit gate: both verification scripts pass and the clean-room compiler includes every new public file.

## Stage 1 — Release integrity and v0.2 candidate

Immediate plan after explicit promotion approval: `docs/plans/01-release-integrity.md`

Outcomes:

- coherent public README, installation, and five-minute synthetic demo;
- deterministic clean-checkout tests and package smoke tests;
- direct tests around optional research adapters;
- a clean compiled public tree created without merging unrelated histories;
- privacy, dependency, secret, and artifact review before any publication.

Exit gate: CI and `scripts/verify_release.sh` pass from both the upgraded source tree and the compiled public candidate; owner approves the exact candidate tree.

## Stage 2 — Trust semantics

Repair:

- `doctor`/validation disagreement;
- index completeness and degraded-state reporting;
- negative-evidence semantics when invalid records are skipped;
- stale index and generation reporting;
- research egress, URL validation, timeout, response-size, and receipt behavior.

Exit gate:

- invalid canon cannot produce an all-green doctor report;
- degraded retrieval is explicit and tested;
- network paths have focused unit tests with no live network;
- threat-model review passes.

## Stage 3 — Public onboarding and demo

Deliver:

- populated fictional scenario spanning source, claim, relationship, interaction, decision, opportunity, inquiry, and research receipt;
- one-command demo initialization;
- expected output and troubleshooting documentation;
- terminal recording or screenshots generated from the synthetic vault.

Exit gate: a clean machine reaches meaningful, cited output within five minutes without private data or a cloud key.

## Stage 4 — Hybrid retrieval and citation UX

Deliver one vector path only:

- FTS5 plus `sqlite-vec`;
- provider-neutral embedding interface;
- local embedding example;
- sensitivity-separated indexes;
- reciprocal-rank fusion and optional reranking;
- `search --explain` and anchored citations;
- fixed retrieval evaluation corpus.

Exit gate: rebuild, deletion, staleness, sensitivity isolation, and retrieval-quality tests pass.

## Stage 5 — Temporal BI and monitored connectors

Deliver incrementally:

- temporal observations and events;
- watchlists and snapshot/diff contracts;
- official-source connectors first;
- entity resolution and source-family deduplication;
- derived analytics and cited briefs.

Exit gate: every signal resolves to preserved evidence and a connector receipt; licensing and egress metadata are explicit.

## Stage 6 — Local visual surfaces

Deliver read-heavy local views before workflow builders:

- review queue and source viewer;
- entity graph and timeline;
- watchlist changes;
- decision and opportunity views.

React Flow may support workflow editing later. ComfyUI is an optional processing integration, not the primary product shell.

## Stage 7 — Knowledge lifecycle

Deepen the evidence-first answer to the "LLM wiki" pattern: knowledge that ages, strengthens, and resolves under explicit rules instead of silently rotting. Approved direction 2026-07-29; full plan in the private maintenance docs.

Deliver:

- first-class `supersedes` links between claims: typed, timestamped, old version preserved and marked stale, queryable ("what changed about X") — **DELIVERED 2026-07-30** (`constellation claim supersede/chain`, journaled ledger, force-via-review on already-stale);
- confidence as a living value: decays with time, strengthens with each confirming source, feeds retrieval ranking and briefing confidence — **DELIVERED 2026-07-30** (`confidence.py` computed score; retrieval tie-break + briefing display; canonical base never overwritten);
- contradiction detection with resolution proposals (recency + source authority + support count) staged as review-only candidates — the human overrides, never the model — **DELIVERED 2026-07-30** (`claim contradictions [--stage]`; promotion applies 7.1 supersedes edges);
- crystallization: completed work sessions distilled into structured digests that enter the candidate pipeline through the normal review gate;
- self-healing lint `--fix` for mechanical repairs (orphan links, broken references), journaled and reversible, with non-mechanical findings still reported only;
- ingest-time secret/PII screening before source material enters the vault;
- multi-writer merge semantics for concurrent agents/profiles: expected-hash writes, timestamp resolution, conflicts surfaced for review.

Exit gate: every lifecycle transition is journaled, reversible, and review-gated; no canonical record changes without an auditable trail; synthetic vault demonstrates supersession, decay, contradiction resolution, and crystallization end-to-end.

## Explicitly deferred

Do not introduce these before their stage has a measured need:

- Neo4j, Kafka, or a workflow orchestrator;
- multiple vector databases;
- automatic GraphRAG-to-canonical promotion;
- unrestricted autonomous crawling;
- silent browser fallback;
- a generic chat UI as the headline product;
- automatic Kanban decomposition or agent swarms.

## Updating this roadmap

Only change stage status after executing the stated gate. Record exact commands and results in the commit/PR or Kanban handoff. Do not put temporary progress in Hermes persistent memory.
