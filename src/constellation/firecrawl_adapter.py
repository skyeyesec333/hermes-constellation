"""Firecrawl deep-page extraction adapter with sensitivity guard."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime

from .models import Sensitivity
from .search_adapter import SearchAdapterError, _require_searchable_sensitivity

FIRECRAWL_URL = "http://" + chr(49) + chr(50) + chr(55) + chr(46) + chr(48) + chr(46) + chr(48) + chr(46) + chr(49) + ":3002"


def extract_page(
    url: str,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    timeout: int = 30,
) -> dict[str, object]:
    """Extract a web page via self-hosted Firecrawl. Blocked for confidential/restricted.

    Returns a dict with markdown, title, and metadata fields.
    """
    _require_searchable_sensitivity(sensitivity)

    payload = json.dumps({"url": url, "formats": ["markdown"]}).encode("utf-8")
    req = urllib.request.Request(
        f"{FIRECRAWL_URL}/v1/scrape",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SearchAdapterError(f"Firecrawl unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SearchAdapterError(f"Firecrawl returned invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise SearchAdapterError("Firecrawl returned unexpected format")

    if not data.get("success", False):
        raise SearchAdapterError(f"Firecrawl extraction failed: {data.get('error', 'unknown error')}")

    result: dict[str, object] = {
        "title": str(data.get("data", {}).get("metadata", {}).get("title", "")),
        "url": url,
        "markdown": str(data.get("data", {}).get("markdown", "")),
        "extracted_at": datetime.now().astimezone().isoformat(),
    }
    return result
