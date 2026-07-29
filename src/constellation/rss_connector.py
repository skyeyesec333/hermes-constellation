"""Egress-gated RSS/Atom connector for watchlist runs.

Safety contract (identical to HttpConnector):
- authorization is checked BEFORE any network attempt, and the default
  authorizer denies everything (fail closed — wire egress explicitly);
- every feed URL passes ``url_safety.validate_http_url`` (scheme +
  SSRF/private address controls) before fetching;
- explicit RunCaps bound items and bytes; truncation is reported truthfully.

Unlike HttpConnector (one item per URL), an RSS/Atom feed emits ONE
ConnectorItem PER ENTRY (title + summary + link + published) so the
snapshot diff sees per-entry materiality. Malformed XML raises
WatchlistError — no partial items are emitted from a failed parse.

Parsing uses stdlib ``xml.etree.ElementTree`` only (no new deps).
Supported: RSS 2.0 ``<item>`` and Atom ``<entry>``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from .url_safety import UnsafeUrlError, validate_http_url
from .watchlists import ConnectorItem, RunCaps, WatchlistError

Fetcher = Callable[[str, int], bytes]
Authorizer = Callable[[str], None]

_REQUEST_TIMEOUT = 30
_ATOM = "{http://www.w3.org/2005/Atom}"


def _deny_all(url: str) -> None:
    raise WatchlistError(
        f"egress not authorized for {url}: wire an explicit authorizer "
        "through the vault egress policy before enabling RSS watch runs"
    )


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "entry"


def _entry(title: str, summary: str, link: str, published: str) -> dict[str, str]:
    return {"title": title, "summary": summary, "link": link, "published": published}


def _parse_rss20(root: ET.Element) -> list[dict[str, str]]:
    entries = []
    for item in root.iter("item"):
        entries.append(_entry(
            title=_text(item.find("title")),
            summary=_text(item.find("description")),
            link=_text(item.find("link")),
            published=_text(item.find("pubDate")),
        ))
    return entries


def _parse_atom(root: ET.Element) -> list[dict[str, str]]:
    entries = []
    for entry in root.iter(f"{_ATOM}entry"):
        link = ""
        fallback = ""
        for link_el in entry.findall(f"{_ATOM}link"):
            href = (link_el.get("href") or "").strip()
            if not href:
                continue
            if link_el.get("rel", "alternate") == "alternate":
                link = href
                break
            fallback = fallback or href
        entries.append(_entry(
            title=_text(entry.find(f"{_ATOM}title")),
            summary=_text(entry.find(f"{_ATOM}summary"))
                    or _text(entry.find(f"{_ATOM}content")),
            link=link or fallback,
            published=_text(entry.find(f"{_ATOM}published"))
                      or _text(entry.find(f"{_ATOM}updated")),
        ))
    return entries


def parse_feed(body: bytes) -> list[dict[str, str]]:
    """Parse one RSS 2.0 or Atom feed payload into entry dicts.

    Raises WatchlistError on malformed XML or an unsupported root element —
    never returns partial results from a failed parse.
    """
    try:
        root = ET.fromstring(body)  # noqa: S314 — defused semantics not needed:
        # payloads come from egress-authorized feeds; ET fromstring is the
        # stdlib-safe surface (no external entity resolution).
    except ET.ParseError as exc:
        raise WatchlistError(f"malformed feed XML: {exc}") from exc
    if root.tag == "rss":
        return _parse_rss20(root)
    if root.tag == f"{_ATOM}feed":
        return _parse_atom(root)
    raise WatchlistError(f"unsupported feed root element: {root.tag!r}")


class RssConnector:
    """WatchConnector over declared RSS/Atom feed URLs, one item per entry."""

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
        return "rss"

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

            body = self._fetcher(url, _REQUEST_TIMEOUT)
            if total_bytes + len(body) > caps.max_bytes:
                truncated = True
                break
            total_bytes += len(body)

            for entry in parse_feed(body):
                if len(items) >= caps.max_items:
                    truncated = True
                    break
                items.append(ConnectorItem(
                    label=f"{len(items) + 1:03d}-{_slug(entry['title'])}.txt",
                    text=(
                        f"title: {entry['title']}\n"
                        f"published: {entry['published']}\n"
                        f"link: {entry['link']}\n"
                        f"feed: {url}\n\n"
                        f"{entry['summary']}"
                    ),
                    source_path=Path(entry["link"] or url),
                ))
            if truncated:
                break
        return items, truncated
