"""Egress-gated HTTP connector for watchlist runs.

Safety contract:
- authorization is checked BEFORE any network attempt, and the default
  authorizer denies everything (fail closed — wire egress explicitly);
- every URL passes ``url_safety.validate_http_url`` (scheme + SSRF/private
  address controls) before fetching;
- explicit RunCaps bound items and bytes; truncation is reported truthfully.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from .url_safety import UnsafeUrlError, validate_http_url
from .watchlists import ConnectorItem, RunCaps, WatchlistError

Fetcher = Callable[[str, int], bytes]
Authorizer = Callable[[str], None]

_REQUEST_TIMEOUT = 30


def _deny_all(url: str) -> None:
    raise WatchlistError(
        f"egress not authorized for {url}: wire an explicit authorizer "
        "through the vault egress policy before enabling HTTP watch runs"
    )


class HttpConnector:
    """WatchConnector over declared HTTP(S) URLs."""

    def __init__(
        self,
        urls: list[str],
        *,
        fetcher: Fetcher,
        authorize: Authorizer | None = None,
    ) -> None:
        self._urls = [str(u) for u in urls]
        self._fetcher = fetcher
        self._authorize = authorize or _deny_all

    def name(self) -> str:
        return "http"

    def fetch(self, caps: RunCaps) -> tuple[list[ConnectorItem], bool]:
        items: list[ConnectorItem] = []
        truncated = False
        total_bytes = 0
        for url in self._urls:
            scheme = urlsplit(url).scheme.lower()
            if scheme not in {"http", "https"}:
                raise WatchlistError(f"URL scheme not allowed: {url}")
            try:
                validate_http_url(url)
            except UnsafeUrlError as exc:
                raise WatchlistError(f"unsafe URL blocked: {url} ({exc})") from exc
            self._authorize(url)  # before any network attempt

            if len(items) >= caps.max_items:
                truncated = True
                break
            body = self._fetcher(url, _REQUEST_TIMEOUT)
            if total_bytes + len(body) > caps.max_bytes:
                truncated = True
                break
            total_bytes += len(body)
            label = urlsplit(url).path.strip("/").replace("/", "-") or "index"
            items.append(ConnectorItem(
                label=f"{label}.txt",
                text=body.decode("utf-8", errors="replace"),
                source_path=Path(url),
            ))
        return items, truncated
