# Installation

Constellation is currently an unreleased local alpha. The wheel is the supported installation artifact; the repository checkout is only for development.

## Build and verify the wheel

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,pdf]'
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python -m build --wheel --no-isolation
```

## Install the standalone CLI

Install the wheel into the Python environment that should own the `constellation` command:

```bash
python -m pip install dist/hermes_constellation-0.1.0a1-py3-none-any.whl
constellation --help
```

Install the `pdf` extra when native-text PDF extraction is required:

```bash
python -m pip install 'hermes-constellation[pdf] @ file:///absolute/path/to/hermes_constellation-0.1.0a1-py3-none-any.whl'
```

## Enable the Hermes plugin

Install the same wheel into the Python environment that runs `hermes`. Hermes discovers the package through the `hermes_agent.plugins` entry point.

```bash
python -m pip install dist/hermes_constellation-0.1.0a1-py3-none-any.whl
hermes plugins enable constellation
hermes plugins list
```

Restart the affected Hermes CLI or gateway process after enabling the plugin. Then verify the real vault explicitly:

```bash
hermes constellation doctor --vault /absolute/path/to/vault
hermes constellation validate --vault /absolute/path/to/vault
```

The wheel includes:

- the provider-neutral Constellation Python core;
- the `constellation` console command;
- the `hermes_agent.plugins` entry point;
- the five bounded Hermes tools;
- `/constellation` and `hermes constellation` command registration;
- the bundled `plugin:constellation` skill and its references;
- the plugin manifest.

Core operation is offline. OCR, vectors, browsers, cloud models, and background schedulers are not installed or activated by this package.

No remote repository or package registry has been created. Publication remains an explicit approval gate.
