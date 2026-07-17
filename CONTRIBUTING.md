# Contributing

Read `AGENTS.md` and `ROADMAP.md` before changing code. The repository is designed to be continued by humans and different coding models without relying on conversation history.

## Development setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
scripts/verify_fast.sh
```

Python 3.11 and 3.12 are supported.

## Work rules

- Work on one roadmap task per branch.
- Preserve unrelated local changes and untracked files.
- Add focused tests for behavior changes.
- Use fictional data and reserved `example.test` domains.
- Keep network calls out of unit tests.
- Do not commit runtime vault state, databases, credentials, browser data, or private records.
- Do not weaken provenance, validation, sensitivity, egress, or clean-room release controls.

## Required checks

Before requesting review:

```bash
scripts/verify_fast.sh
```

Also run the full gate for packaging, release, manifest, privacy, CLI, demo, or dependency changes:

```bash
scripts/verify_release.sh
```

Report the exact commands and real results in the pull request. A model-generated assurance is not verification.

## Commit and review scope

Keep commits small and coherent. Do not use `git add -A` when unrelated files are present. Security-sensitive changes listed in `AGENTS.md` require an independent review.

The upgraded development history and public `origin/main` are unrelated. Do not merge or rebase them. Public release candidates are compiled through `resources/public-lineage.yaml` and reviewed as clean trees.
