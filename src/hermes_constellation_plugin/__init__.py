"""Hermes plugin registration for Constellation."""

from __future__ import annotations

import argparse
import contextlib
import io
import shlex
from pathlib import Path
from typing import Any

from . import schemas, tools

_TOOLS = (
    ("constellation_status", schemas.STATUS_SCHEMA, tools.handle_status, "🧭"),
    ("constellation_ingest", schemas.INGEST_SCHEMA, tools.handle_ingest, "📥"),
    ("constellation_validate", schemas.VALIDATE_SCHEMA, tools.handle_validate, "✅"),
    ("constellation_search", schemas.SEARCH_SCHEMA, tools.handle_search, "🔎"),
    ("constellation_review", schemas.REVIEW_SCHEMA, tools.handle_review, "🧾"),
)


def _run_cli_args(argv: list[str]) -> str:
    from constellation.cli import main

    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            main(argv)
        return output.getvalue().strip()
    except SystemExit as exc:
        return f"constellation command exited with status {exc.code}"
    except Exception as exc:
        return f"constellation command failed: {type(exc).__name__}: {exc}"


def _handle_slash(raw_args: str) -> str:
    if not raw_args.strip():
        return "Usage: /constellation <init|doctor|ingest|validate|index|search|review|research> ..."
    return _run_cli_args(shlex.split(raw_args))


def _setup_cli(parser: Any) -> None:
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to the Constellation CLI",
    )


def _handle_cli(namespace: Any) -> int:
    print(_run_cli_args(list(namespace.args)))
    return 0


def register(ctx) -> None:
    """Register bounded tools plus slash and CLI commands."""
    package_skill = Path(__file__).resolve().parent / "skills" / "constellation" / "SKILL.md"
    checkout_skill = Path(__file__).resolve().parents[2] / "skills" / "constellation" / "SKILL.md"
    skill_path = package_skill if package_skill.is_file() else checkout_skill
    if not skill_path.is_file():
        raise FileNotFoundError("bundled Constellation skill is missing")
    ctx.register_skill(
        "constellation",
        skill_path,
        "Operate a local source-grounded Constellation vault.",
    )
    for name, base_schema, handler, emoji in _TOOLS:
        schema = {"name": name, **base_schema}
        ctx.register_tool(
            name=name,
            toolset="constellation",
            schema=schema,
            handler=handler,
            emoji=emoji,
        )
    ctx.register_command(
        "constellation",
        handler=_handle_slash,
        description="Operate a local source-grounded Constellation vault.",
    )
    ctx.register_cli_command(
        name="constellation",
        help="Operate a local source-grounded Constellation vault",
        setup_fn=_setup_cli,
        handler_fn=_handle_cli,
        description="Initialize, ingest, validate, search, and review a Constellation vault.",
    )
