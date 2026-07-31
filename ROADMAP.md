# Constellation implementation roadmap

This file is the durable cross-model handoff. Conversation history is not required.

## Active stage

**Stage 1 — release integrity and v0.2 candidate**

Status: `blocked — waiting for explicit private acceptance/public-promotion approval`

Immediate plan: `docs/plans/01-release-integrity.md`

Reality check (2026-07-30): build work raced ahead of this header — Stage 7
knowledge-lifecycle is COMPLETE (all seven items delivered), and Stage 5/6
connectors (EDGAR, Polymarket) are live-fired. The Stage 1 gate above is the
ONLY thing between the repo and Stage 3 public onboarding. Open items below
the fold: S6-B deferred items (React Flow editing). Thai retrieval evaluated
2026-07-30 (ab4010c): harness + fixture proof + live verdict — unicode61
kept (trigram regresses English 0.97→0.75); Thai gap latent (0 Thai-script
titles, 18 records with Thai bodies); Thai path = per-language/dual-index,
owner decision. Closed 2026-07-30: entity resolution/source-family dedup
(entity_resolution.py + `resolve scan|stage`, 0ccf3d8), derived analytics
depth (analytics.py in briefings, 16212a0), RSS standing schedule
(constellation-weekly-watch cron, Mon 08:00), Beyond-Round-3 cheap version
(constellation-journey-regression cron, daily 07:00). See
maintenance/HANDOFF-2026-07-30-items-2-3-4-7.md in the CSO vault.

i2-successor graph intelligence program (private, owner-approved 2026-07-30;
spec in CSO vault maintenance/SPEC-i2-successor-graph-intelligence-program-2026-07-30.md):
private implementation lane parallel to the blocked public Stage 1 — no push,
no public promotion. Wave 0 (reconcile/inventory/freeze contracts): Task 0.1
predicate inventory delivered (scripts/predicate_inventory.py; deterministic,
read-only, metadata-only; private-vault run saved to /tmp only). Task 0.2
contracts frozen (docs/graph-intelligence.md +
docs/references/graph-intelligence-sources.md). Wave 1 (trustworthy
relationship semantics) COMPLETE 2026-07-31: predicate registry
(resources/predicates/core.yaml + predicates.py; confidence decay consumes
the shared stability lookup), temporal/qualifier RelationshipRecord schema,
review-gated relationship pipeline (stage/list/supersede + promotion through
the existing review machinery, idempotent envelope candidates with
assertion fingerprints), and temporal directed graph filters with stable
sha256 edge_ids (b5d9e29, 467da51, 23239a6, c1f4da5). Wave 2
(compatibility, mentions, backfill) COMPLETE 2026-07-31: bounded FtM
exchange adapter (87d7ed4), entity-resolution warninglists (68455f7),
evidence-anchored mention cross-reference (8651981), conservative
relationship backfill planner (b3709b7). Private-vault read-only runs:
56/56 entity-to-entity claims eligible; plan = 51 create proposals (0
unresolved, hash-guarded, HELD for owner review — not staged); FtM export
505 entities at internal ceiling (32 excluded by sensitivity). Wave 3
(graph analytics) COMPLETE 2026-07-31: deterministic graph model +
optional networkx extra (7855a2d), SNA metrics with immutable analysis
receipts (a7285cc), bounded all-shortest typed paths (ab9cb14), briefing
network_position with degraded no-networkx fallback (7a05c46, 7301d56).
Wave 4 (investigation layer) COMPLETE 2026-07-31: deterministic typology
detection from canonical graph shape (5b8258a), review-only hypothesis
packets with expiry/falsification/review trail (2a198a3), bounded graph
delta snapshots/diffs + script-only watchdog (a3f4a70). Wave 5 (dashboard)
COMPLETE 2026-07-31 in the dashboard plugin repo: read-model analytics API
(36b5474), graph explorer canvas with filters/path mode/investigation
panels (e2f1ac8), host-env-independent analytics serving (3812496),
journey harness coverage (2f81999 core). Live browser smoke + full journey
watchdog green. Wave 6 (exchange and decay) COMPLETE 2026-07-31: bounded
graph export with evidence manifest (230ffd8), import-only connector
contracts (bcd61d8), relationship decay reports with staged review-gated
suggestions (a1439cf). Baseline at
program start: core 874c6a6, dashboard 6b6f07b, verify_fast green.

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
- official-source connectors first — SEC EDGAR live-fired 2026-07-30 (CoreWeave CIK 1769628, 50 filing items, snapshot + receipts); Polymarket Gamma connector DELIVERED + live-fired 2026-07-30 (17 real markets, `watch-collect --polymarket-query`, egress-gated, owner-approved provider);
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
- crystallization: completed work sessions distilled into structured digests that enter the candidate pipeline through the normal review gate — **DELIVERED 2026-07-30** (`constellation crystallize`; deterministic digest, provenance-cited, review-only; real CSO artifact staged as acceptance);
- self-healing lint `--fix` for mechanical repairs (orphan links, broken references), journaled and reversible, with non-mechanical findings still reported only — **DELIVERED 2026-07-30** (`lint --fix/--rollback`; single-unambiguous-target rule; byte-exact rollback replay);
- ingest-time secret/PII screening before source material enters the vault — **DELIVERED 2026-07-30** (`screening.py`; strict blocks secrets pre-preservation, PII warns, quarantine/off profiles, never silent strip);
- multi-writer merge semantics for concurrent agents/profiles: expected-hash writes, timestamp resolution, conflicts surfaced for review — **DELIVERED 2026-07-30** (`constellation merge`; per-field compare-and-swap; conflicts stage review candidates, no last-write-wins).

**STAGE 7 COMPLETE 2026-07-30** — all seven items delivered (7.1 supersedes, 7.2 confidence, 7.3 contradictions, 7.4 crystallization, 7.5 lint --fix, 7.6 ingest screening, 7.7 merge).

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
