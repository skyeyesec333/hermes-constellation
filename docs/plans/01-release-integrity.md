# Stage 1 plan: release integrity and v0.2 candidate

Goal: turn the upgraded development line into a reproducible, reviewable public v0.2 candidate without merging unrelated Git histories or exposing private material.

Do not begin until Stage 0 in `ROADMAP.md` is complete **and Bryan has explicitly approved promotion after the relevant private behavior has worked for him and Aiko**. Private implementation or successful dogfooding by itself is not public-promotion authorization.

## Constraints

- Public work uses only repository files and fictional fixtures.
- The active task or roadmap must record explicit owner approval for public promotion.
- Never mount, inspect, or copy a private vault into this worktree.
- Do not merge, rebase, or force-push `origin/main`.
- Do not publish, tag, or push the candidate without owner approval.
- Do not weaken the lineage manifest or privacy audit to admit an unknown file.
- Unit and CI tests must not require live web services.

If approval is absent or ambiguous, stop without editing product behavior and leave the public task blocked.

## Task 1.1 — Freeze and document the source baseline

Files:

- `README.md`
- `docs/installation.md`
- `docs/architecture.md`
- `ROADMAP.md`

Actions:

1. Capture branch, HEAD, `origin/main`, and absence of a merge base.
2. Run both verification scripts and retain the real outputs in the task handoff.
3. Reconcile README claims with implemented CLI commands and tests.
4. Remove or qualify claims that are not exercised by a deterministic test.

Acceptance:

- README does not claim unavailable or untested behavior.
- Product positioning remains local-first, provenance-first, and evidence-grade rather than generic RAG.
- `scripts/verify_fast.sh` passes.

## Task 1.2 — Complete installation and five-minute quickstart

Files:

- `README.md`
- `docs/installation.md`
- `examples/synthetic-demo-vault/**`
- relevant CLI tests

Actions:

1. Document source install and wheel install separately.
2. Provide exact commands for init, doctor, ingest, review, index, search, and validate.
3. Use only `example.test` identities and fictional organizations.
4. Add expected output shapes without hard-coding unstable timestamps or IDs.

Acceptance:

- Commands run successfully in the release smoke environment.
- Demo validation reports zero invalid records.
- No cloud API key is required.

## Task 1.3 — Test the optional research boundary

Files:

- `src/constellation/research_runner.py`
- `src/constellation/search_adapter.py`
- `src/constellation/firecrawl_adapter.py`
- new focused tests
- `docs/threat-model.md`
- `docs/integrations.md`

Required cases:

- restricted/confidential sensitivity fails closed before network access;
- malformed and non-HTTP(S) URLs are rejected;
- localhost/private/link-local destinations are rejected where applicable;
- timeouts and response-size limits are enforced;
- unavailable adapters fall through only as documented;
- no live network is used by tests;
- receipts distinguish complete, partial, and failed collection.

Acceptance:

- Focused tests cover the escalation order and every fail-closed boundary.
- Threat model matches current behavior.
- Full verification passes.

## Task 1.4 — Strengthen CI and dependency/security evidence

Files:

- `.github/workflows/**`
- `pyproject.toml`
- verification scripts
- `SECURITY.md`

Actions:

1. Test Python 3.11 and 3.12.
2. Run Ruff, pytest, package build, clean-room compile, privacy audit, and fresh-wheel smoke.
3. Add dependency auditing and source scanning without granting write permissions.
4. Pin third-party actions to stable major versions or immutable commits where practical.

Acceptance:

- Pull-request CI needs read-only repository permissions.
- Every required check passes from a clean checkout.
- Security tooling does not scan or upload private filesystem content.

## Task 1.5 — Compile the clean public candidate

Inputs:

- upgraded source line;
- `resources/public-lineage.yaml`;
- fictional public fixtures only.

Actions:

1. Compile to a new directory outside the repository.
2. Verify the compiler report and privacy audit.
3. Initialize a fresh Git repository from the compiled tree; do not preserve private/upgraded history.
4. Install and test the wheel from that exact tree.
5. Produce a file/hash inventory for owner review.

Acceptance:

- Candidate tree exactly matches the allowlist.
- Source and wheel smoke tests pass.
- No symlink, database, browser state, real contact, private path, credential, or non-example fixture exists.
- Owner can inspect the exact local candidate before any remote action.

## Task 1.6 — Independent release-boundary review

Reserve a strong fresh-context reviewer for this task.

Review:

- release compiler and manifest changes;
- privacy scanner coverage;
- package contents and entry points;
- README/installation accuracy;
- research adapter threat boundary;
- CI permissions and third-party actions;
- candidate file/hash inventory.

Acceptance:

- No unresolved critical/high finding.
- Deterministic gates remain green after fixes.
- Owner explicitly approves publication; otherwise stop with the local candidate intact.

## Required handoff

```json
{
  "changed_files": [],
  "verification": [],
  "candidate_path": null,
  "candidate_tree_sha256": null,
  "decisions": [],
  "residual_risk": [],
  "next_task": null
}
```

Never place secrets, private paths, raw audit payloads, or private fixture details in the handoff.
