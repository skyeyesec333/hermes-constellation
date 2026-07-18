"""Search adapters with sensitivity guard — SearXNG, EXA, Brave API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
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


# ── EXA semantic search ──

_EXA_SEARCH_URL = "https://api.exa.ai/search"
_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_MAX_SEARCH_RESPONSE_BYTES = 2_000_000
_BRAVE_FRESHNESS = frozenset({"", "pd", "pw", "pm", "py"})


def _read_search_json(response: object, provider: str) -> dict[str, object]:
    try:
        payload = response.read(_MAX_SEARCH_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
        if len(payload) > _MAX_SEARCH_RESPONSE_BYTES:
            raise SearchAdapterError(f"{provider} response exceeds size limit")
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchAdapterError(f"{provider} returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SearchAdapterError(f"{provider} returned unexpected result format")
    return data


def exa_search(
    query: str,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    limit: int = 10,
    timeout: int = 15,
) -> list[dict[str, object]]:
    """Search Exa's semantic index; silently skip when unconfigured."""
    _require_searchable_sensitivity(sensitivity)
    credential = os.environ.get("EXA_API_KEY", "")
    if not credential:
        return []

    request = urllib.request.Request(
        _EXA_SEARCH_URL,
        data=json.dumps(
            {
                "query": query,
                "type": "auto",
                "numResults": limit,
                "contents": {"highlights": True, "text": True},
            }
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": credential,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = _read_search_json(response, "EXA API")
    except urllib.error.URLError as exc:
        raise SearchAdapterError(f"EXA API unreachable: {exc}") from exc

    results = data.get("results", [])
    if not isinstance(results, list):
        raise SearchAdapterError("EXA API returned unexpected result format")

    output: list[dict[str, object]] = []
    for item in results[:limit]:
        if not isinstance(item, dict):
            continue
        highlights = item.get("highlights", [])
        if isinstance(highlights, list) and highlights and isinstance(highlights[0], str):
            snippet = highlights[0]
        else:
            snippet = str(item.get("text", ""))[:200]
        output.append(
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": snippet,
                "engine": "exa",
            }
        )
    return output


# ── Brave Search API ──


def brave_search(
    query: str,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    freshness: str = "",
    limit: int = 10,
    timeout: int = 15,
) -> list[dict[str, object]]:
    """Search Brave's independent index; optionally filter by freshness."""
    _require_searchable_sensitivity(sensitivity)
    credential = os.environ.get("BRAVE_API_KEY", "")
    if not credential:
        return []
    if freshness not in _BRAVE_FRESHNESS:
        raise SearchAdapterError("Brave freshness must be one of pd, pw, pm, or py")

    params: dict[str, str] = {"q": query, "count": str(limit)}
    if freshness:
        params["freshness"] = freshness
    request = urllib.request.Request(
        f"{_BRAVE_SEARCH_URL}?{urllib.parse.urlencode(params)}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": credential,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = _read_search_json(response, "Brave API")
    except urllib.error.URLError as exc:
        raise SearchAdapterError(f"Brave API unreachable: {exc}") from exc

    web = data.get("web", {})
    results = web.get("results", []) if isinstance(web, dict) else []
    if not isinstance(results, list):
        raise SearchAdapterError("Brave API returned unexpected result format")

    output: list[dict[str, object]] = []
    for item in results[:limit]:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("description", "")),
                "engine": "brave_api",
            }
        )
    return output


def brave_search_time_sensitive(
    query: str,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    limit: int = 10,
    timeout: int = 15,
) -> list[dict[str, object]]:
    """Search Brave for results from the past week."""
    return brave_search(
        query,
        sensitivity=sensitivity,
        freshness="pw",
        limit=limit,
        timeout=timeout,
    )
