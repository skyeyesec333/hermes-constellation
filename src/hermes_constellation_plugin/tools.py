"""Bounded JSON tool handlers for the Constellation plugin."""

from __future__ import annotations

import json
from typing import Any


def _result(ok: bool, **payload: Any) -> str:
    return json.dumps({"version": 1, "ok": ok, **payload}, ensure_ascii=False, default=str)


def _handle(action: str, args: dict[str, Any] | None, **_: Any) -> str:
    values = args if isinstance(args, dict) else {}
    if not values.get("vault"):
        return _result(False, error="vault is required")
    try:
        from constellation.cli import run_action

        return _result(True, result=run_action(action, values))
    except Exception as exc:
        return _result(False, error=f"{type(exc).__name__}: {exc}")


def handle_status(args: dict[str, Any], **kwargs: Any) -> str:
    return _handle("doctor", args, **kwargs)


def handle_ingest(args: dict[str, Any], **kwargs: Any) -> str:
    return _handle("ingest", args, **kwargs)


def handle_validate(args: dict[str, Any], **kwargs: Any) -> str:
    return _handle("validate", args, **kwargs)


def handle_search(args: dict[str, Any], **kwargs: Any) -> str:
    return _handle("search", args, **kwargs)


def handle_review(args: dict[str, Any], **kwargs: Any) -> str:
    return _handle("review", args, **kwargs)
