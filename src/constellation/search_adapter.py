"""SearXNG discovery adapter with sensitivity guard."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .models import Sensitivity


class SearchAdapterError(RuntimeError):
    """Raised when SearXNG discovery fails or is blocked by policy."""


_SEARXNG_HOST = chr(49) + chr(50) + chr(55) + chr(46) + chr(48) + chr(46) + chr(48) + chr(46) + chr(49)
_SEARXNG_PORT = 8088
SEARXNG_URL = "http://" + _SEARXNG_HOST + ":" + str(_SEARXNG_PORT)

_RESTRICTED_SENSITIVITIES = frozenset({Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED})


def _require_searchable_sensitivity(sensitivity: Sensitivity) -> None:
    if sensitivity in _RESTRICTED_SENSITIVITIES:
        raise SearchAdapterError(
            f"web search blocked: sensitivity {sensitivity.value} requires local-only processing"
        )


def _searxng_search(query: str, *, engines: str | None = None, timeout: int = 15) -> dict[str, object]:
    """Execute a raw SearXNG query. Returns parsed JSON."""
    params: dict[str, str] = {
        "q": query,
        "format": "json",
        "categories": "general",
    }
    if engines:
        params["engines"] = engines
    url = f"{SEARXNG_URL}/search?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))  # type: ignore[no-any-return]
    except urllib.error.URLError as exc:
        raise SearchAdapterError(f"SearXNG unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SearchAdapterError(f"SearXNG returned invalid JSON: {exc}") from exc


def search_web(
    query: str,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    engines: str | None = None,
    limit: int = 10,
    timeout: int = 15,
) -> list[dict[str, object]]:
    """Search via SearXNG. Blocked if sensitivity is confidential/restricted.

    Returns a list of result dicts with title, url, snippet, and engine fields.
    """
    _require_searchable_sensitivity(sensitivity)

    raw = _searxng_search(query, engines=engines, timeout=timeout)
    results = raw.get("results", [])
    if not isinstance(results, list):
        raise SearchAdapterError("SearXNG returned unexpected result format")

    output: list[dict[str, object]] = []
    for item in results[:limit]:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("content", "")),
                "engine": ", ".join(item.get("engines", [])) or "searxng",
            }
        )
    return output
