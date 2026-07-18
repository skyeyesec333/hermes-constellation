"""Hermes plugin registration for Constellation."""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import shlex
from datetime import UTC, datetime
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


_CONFIGURED_VAULT_ACTIONS = frozenset(
    {
        "doctor",
        "operator",
        "strategy",
        "graph",
        "resolve",
        "ingest",
        "preflight",
        "bundle",
        "validate",
        "index",
        "search",
        "review",
        "research",
        "synthesize",
        "claim",
        "interaction",
        "decision",
        "inquiry",
        "opportunity",
        "lead",
        "prep",
        "decay",
        "patterns",
        "search-books",
        "extract-claims",
        "enrich",
        "trail",
        "migrate-plan",
        "migrate-rehearse",
        "migrate-prepare",
        "migrate-activate",
        "migrate-entities",
    }
)


def _looks_like_explicit_vault(value: str) -> bool:
    path = Path(value).expanduser()
    return path.is_absolute() or value.startswith(("./", "../", "~"))


def _handle_slash(raw_args: str) -> str:
    if not raw_args.strip():
        return (
            "Usage: /constellation "
            "<init|doctor|ingest|validate|index|search|review|research|prep|decay|patterns|trail> ..."
        )
    argv = shlex.split(raw_args)
    if argv[0] in _CONFIGURED_VAULT_ACTIONS:
        has_explicit_vault = len(argv) > 1 and _looks_like_explicit_vault(argv[1])
        if not has_explicit_vault:
            vault = _configured_vault()
            if vault is None:
                return _missing_vault()
            argv.insert(1, vault)
    return _run_cli_args(argv)


def _configured_vault() -> str | None:
    raw = os.environ.get("CONSTELLATION_VAULT", "").strip()
    if not raw:
        return None
    return str(Path(raw).expanduser())


def _missing_vault() -> str:
    return "CONSTELLATION_VAULT is not configured for this Hermes runtime"


def _handle_research(raw_args: str) -> str:
    text = raw_args.strip()
    if not text:
        return "Usage: /research <bounded question or URL>\nAdd private context on later lines."
    vault = _configured_vault()
    if vault is None:
        return _missing_vault()

    lines = text.splitlines()
    question = lines[0].strip()
    context = "\n".join(lines[1:]).strip()
    try:
        from constellation.models import Inquiry, Sensitivity
        from constellation.research_runner import run_inquiry

        now = datetime.now(UTC)
        inquiry = Inquiry(
            title=question[:80],
            status="active",
            sensitivity=Sensitivity.INTERNAL,
            question=question,
            why_it_matters=context[:300] if context else "Hermes slash-command inquiry",
            target_scope=context[:200] if context else "general",
            evidence_needed="Primary sources preferred",
            source_priority="primary-and-secondary",
            promotion_policy="review-all",
            max_search_queries=2,
            max_unique_sources=5,
            max_model_calls=2,
            max_pages=5,
            created_at=now,
            updated_at=now,
        )
        result = run_inquiry(
            Path(vault), inquiry, sensitivity=Sensitivity.INTERNAL, max_pages=5
        )
    except Exception as exc:
        return f"Research failed: {type(exc).__name__}: {exc}"

    output = [
        f"Research: {question[:120]}",
        f"Status: {result.get('status', 'unknown')}",
        (
            "Sources: "
            f"{result.get('sources_discovered', 0)} found, "
            f"{result.get('sources_extracted', 0)} extracted, "
            f"{result.get('sources_failed', 0)} failed"
        ),
        f"Run: {str(result.get('run_id', 'unknown'))[:8]}",
        f"Receipt: {result.get('receipt_path', 'unknown')}",
    ]
    preserved_sources = result.get("preserved_sources", [])
    if isinstance(preserved_sources, list):
        for item in preserved_sources[:5]:
            if isinstance(item, dict):
                output.append(
                    f"  - {item.get('source_path', item.get('url', 'preserved source'))}"
                )
    return "\n".join(output)


def _handle_prep(raw_args: str) -> str:
    entity_id = raw_args.strip()
    if not entity_id:
        return "Usage: /prep <entity-id>"
    vault = _configured_vault()
    return _missing_vault() if vault is None else _run_cli_args(["prep", vault, entity_id])


def _handle_decay(raw_args: str) -> str:
    vault = _configured_vault()
    if vault is None:
        return _missing_vault()
    args = shlex.split(raw_args.strip())
    threshold = args[0] if args else "14"
    return _run_cli_args(["decay", vault, "--threshold", threshold])


def _handle_patterns(raw_args: str) -> str:
    vault = _configured_vault()
    if vault is None:
        return _missing_vault()
    args = shlex.split(raw_args.strip())
    minimum = args[0] if args else "2"
    return _run_cli_args(["patterns", vault, "--min-cluster", minimum])


def _handle_trail(raw_args: str) -> str:
    decision_id = raw_args.strip()
    if not decision_id:
        return "Usage: /trail <decision-id>"
    vault = _configured_vault()
    return _missing_vault() if vault is None else _run_cli_args(["trail", vault, decision_id])


def _handle_searchbooks(raw_args: str) -> str:
    query = raw_args.strip()
    if not query:
        return "Usage: /searchbooks <natural-language question>"
    vault = _configured_vault()
    return _missing_vault() if vault is None else _run_cli_args(["search-books", vault, query])


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
    ctx.register_command(
        "research",
        handler=_handle_research,
        description="Run a bounded, egress-gated research inquiry.",
    )
    ctx.register_command(
        "prep",
        handler=_handle_prep,
        description="Compile an operator brief for a configured-vault entity.",
    )
    ctx.register_command(
        "decay",
        handler=_handle_decay,
        description="Detect configured-vault contacts needing follow-up.",
    )
    ctx.register_command(
        "patterns",
        handler=_handle_patterns,
        description="Detect configured-vault cross-entity claim clusters.",
    )
    ctx.register_command(
        "trail",
        handler=_handle_trail,
        description="Trace a configured-vault decision provenance chain.",
    )
    ctx.register_command(
        "searchbooks",
        handler=_handle_searchbooks,
        description="Search books indexed in the configured vault.",
    )
    ctx.register_cli_command(
        name="constellation",
        help="Operate a local source-grounded Constellation vault",
        setup_fn=_setup_cli,
        handler_fn=_handle_cli,
        description="Initialize, ingest, validate, search, and review a Constellation vault.",
    )
