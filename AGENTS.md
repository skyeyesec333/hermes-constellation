# Constellation agent instructions

These instructions are the provider-neutral development contract for Hermes, Kilo Code, Codex, DeepSeek, and human contributors.

## Start every task here

1. Run `git status --short --branch` and preserve pre-existing changes.
2. Read `ROADMAP.md` and identify its single active stage.
3. Read the immediate plan linked from that stage.
4. Read the tests nearest the code before editing it.
5. Work on one bounded task only. Do not start a later stage early.

If the repository state contradicts the roadmap, stop and report the exact contradiction. Git and executable tests outrank prose.

## Private-first promotion model

The private CSO Constellation is the upstream development and acceptance environment. New capabilities are implemented or exercised there first through Bryan's conversations and Aiko's real workflow. The public repository is a downstream clean-room promotion target, not the first deployment target.

- Do not begin public implementation merely because an idea or private change exists.
- Public work requires Bryan's explicit approval that the private behavior works for him and Aiko and is ready for promotion.
- Until that approval is recorded in `ROADMAP.md` or the active task, keep the public task blocked.
- Promotion means generalizing the approved behavior, reproducing it with synthetic fixtures, and passing public release gates.
- Raw private records, paths, prompts, transcripts, and private-only assumptions never cross into the public repository.
- A private success establishes user acceptance; it does not replace clean public tests, packaging, privacy audit, or release review.

## Product contract

Constellation is a local-first, evidence-grade intelligence workspace. Markdown records and preserved source files are canonical. Generated SQLite indexes, caches, receipts, and other `.constellation/` runtime state are disposable or private and must not enter the public distribution.

The trusted write path is:

```text
preserve source -> extract bounded evidence -> stage candidate -> validate -> explicit review/promotion -> rebuild index -> write receipt
```

Keep these evidence classes separate:

- source bytes and mechanical extraction;
- source claims;
- corroborated canonical facts;
- model or analyst inference;
- decisions and opportunities derived from reviewed evidence.

Do not silently promote model output, collapse conflicts, invent identity matches, or treat an empty/degraded retrieval result as proof that evidence does not exist.

## Non-negotiable safety boundaries

- This is a public clean-room repository. Use only fictional fixtures and reserved domains such as `example.test`.
- Never read, copy, summarize, scan, or modify a private Constellation vault or any private Hermes profile while doing public-repository work.
- Never add credentials, `.env` contents, private paths, browser state, real contacts, private logs, generated indexes, or model transcripts.
- Do not weaken the release allowlist, privacy audit, egress gate, validation, expected-hash checks, or atomic-write behavior merely to make a test pass.
- External network access is denied unless the task explicitly concerns a network adapter. Tests must use fakes and must not require live services.
- Do not push, publish, tag, create a release, rewrite shared history, or modify `origin/main` without explicit owner approval.
- Preserve unrelated and untracked user files. Never use `git add -A` in a dirty tree.

## Repository lineage warning

The upgraded development line and public `origin/main` have unrelated Git roots and no merge base. Do not solve this with a normal merge, rebase, force-push, or broad cherry-pick. The public v0.2 candidate must be compiled one way through `resources/public-lineage.yaml`, audited, initialized as a clean tree, and reviewed before publication.

## Development workflow

1. Reproduce or characterize the issue.
2. For behavior changes, add or update a focused test first.
3. Make the smallest coherent change.
4. Run the focused test while iterating.
5. Run `scripts/verify_fast.sh` before declaring the task complete.
6. Run `scripts/verify_release.sh` when release, packaging, manifest, privacy, demo, CLI, or dependency behavior changes.
7. Update `ROADMAP.md` only when an acceptance gate has actually passed.

Do not dispatch child agents for routine implementation. One implementer owns the task. Use an independent reviewer only for security-sensitive or stage-closing changes. A public implementer must also verify that explicit promotion approval exists before editing product behavior.

## Canonical commands

```bash
# Fast deterministic gate
scripts/verify_fast.sh

# Full clean-room/package smoke gate
scripts/verify_release.sh

# Focused test
.venv/bin/python -m pytest tests/test_retrieval.py -q

# CLI surface
.venv/bin/constellation --help

# Compile a public tree outside the repository
.venv/bin/python scripts/build_release.py \
  . /tmp/hermes-constellation-public resources/public-lineage.yaml
```

The scripts select `.venv/bin/python` when available and otherwise use `python3`.

## Architecture map

- `src/constellation/models.py`: canonical schemas and invariants.
- `src/constellation/ingest.py`: preservation, extraction, manifests, source candidates.
- `src/constellation/review.py`: candidate promotion and conflict checks.
- `src/constellation/validation.py`: canonical validation.
- `src/constellation/retrieval.py`: disposable FTS index and evidence search.
- `src/constellation/egress.py`: fail-closed provider/model/purpose/sensitivity authorization.
- `src/constellation/research_runner.py`: optional network research escalation; treat as high risk.
- `src/constellation/release.py` and `src/constellation/privacy.py`: clean-room release boundary.
- `src/hermes_constellation_plugin/`: thin Hermes plugin surface.
- `resources/public-lineage.yaml`: exhaustive public-file allowlist and lineage record.
- `examples/synthetic-demo-vault/`: public fictional fixture only.

Read these before changing trust-sensitive code:

- `docs/architecture.md`
- `docs/threat-model.md`
- `docs/egress-policy.md`
- `docs/clean-room-release.md`
- `docs/first-distillation-contract.md`

## Security-sensitive changes

Require an independent review before merge when touching:

- filesystem roots, paths, symlinks, archives, or atomic writes;
- release compilation, privacy scanning, manifests, or packaging;
- URL fetching, SSRF controls, browser automation, or model egress;
- sensitivity filtering, retrieval completeness, or index freshness;
- candidate promotion, identity resolution, or canonical schemas;
- plugin/MCP tools, subprocesses, or dependencies.

No AI reviewer may waive a failing deterministic gate.

## Completion and handoff

A completion summary must include:

```json
{
  "changed_files": ["relative/path"],
  "verification": ["exact command and result"],
  "decisions": ["non-obvious choice"],
  "residual_risk": ["what remains unverified"],
  "next_task": "next bounded roadmap item or null"
}
```

Never claim success from expected output or inspection alone. Report only commands actually run and their real results.
