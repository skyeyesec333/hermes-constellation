# Contributing

This project is in local alpha and does not yet have a public remote.

Contributions must use synthetic `example.test` data, add behavior-focused tests, preserve Markdown/source canonicality, and pass the exact release-tree privacy audit. Do not submit real vault content, credentials, browser state, local indexes, provider transcripts, or private-derived fixtures.

Run:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check src tests scripts
```
