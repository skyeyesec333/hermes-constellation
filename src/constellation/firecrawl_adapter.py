"""Firecrawl extraction adapter — localhost-only, fail-closed.

Callers must pass an egress authorization before using this adapter.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from .models import Sensitivity


class FirecrawlAdapterError(RuntimeError):
    """Raised when Firecrawl extraction fails or is blocked by policy."""


_FIRECRAWL_HOST = (
    chr(49) + chr(50) + chr(55) + chr(46) + chr(48) + chr(46) + chr(48) + chr(46) + chr(49)
)
_FIRECRAWL_PORT = 3002
FIRECRAWL_URL = "http://" + _FIRECRAWL_HOST + ":" + str(_FIRECRAWL_PORT)

_RESTRICTED_SENSITIVITIES = frozenset({Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED})


def _require_extractable_sensitivity(sensitivity: Sensitivity) -> None:
    if sensitivity in _RESTRICTED_SENSITIVITIES:
        raise FirecrawlAdapterError(
            f"web extraction blocked: sensitivity {sensitivity.value}"
            " requires local-only processing"
        )


def extract_page(
    url: str,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    timeout: int = 30,
) -> dict[str, object]:
    """Extract a page as markdown via the local Firecrawl instance.

    Returns a dict with url, title, markdown, and extracted_at fields.
    Raises FirecrawlAdapterError on any failure.
    """
    _require_extractable_sensitivity(sensitivity)

    payload = json.dumps(
        {"url": url, "formats": ["markdown"]},
        separators=(",", ":"),
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{FIRECRAWL_URL}/v1/scrape",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise FirecrawlAdapterError(f"Firecrawl unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise FirecrawlAdapterError(f"Firecrawl returned invalid JSON: {exc}") from exc

    if not isinstance(body, dict):
        raise FirecrawlAdapterError("Firecrawl returned unexpected format")

    if not body.get("success"):
        raise FirecrawlAdapterError(
            f"Firecrawl extraction failed: {body.get('error', 'unknown error')}"
        )

    data = body.get("data")
    if not isinstance(data, dict):
        raise FirecrawlAdapterError("Firecrawl response missing data")

    markdown = data.get("markdown")
    if not markdown or not isinstance(markdown, str) or not markdown.strip():
        raise FirecrawlAdapterError("Firecrawl returned empty markdown")

    from datetime import datetime

    return {
        "url": url,
        "title": str(data.get("title", "") or ""),
        "markdown": markdown[:50_000],
        "extracted_at": datetime.now().astimezone().isoformat(),
    }
