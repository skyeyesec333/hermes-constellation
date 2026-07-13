# Installation

This repository is an unreleased local alpha.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/constellation --help
```

The eventual Hermes installation uses the standalone plugin in this repository. No remote repository or package registry has been created yet.

Core operation is offline. OCR, vectors, browsers, Docker, and background schedulers are optional future capabilities, not installation requirements.
