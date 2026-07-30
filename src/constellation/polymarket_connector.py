"""Egress-gated Polymarket Gamma connector for watchlist runs.

Safety contract (identical to EdgarConnector / HttpConnector / RssConnector):
- authorization is checked BEFORE any network attempt, and the default
  authorizer denies everything (fail closed — wire egress explicitly);
- request URLs are constructed from validated, non-empty query terms only
  and still pass ``url_safety.validate_http_url`` before fetching;
- explicit RunCaps bound items and bytes; truncation is reported truthfully.

A Gamma ``/public-search`` payload emits ONE ConnectorItem PER MARKET
(question + parsed Yes/No prices + volume + event link) so the snapshot
diff sees per-market materiality — a price move appears as a changed item.
Malformed JSON, a payload missing ``events``, or corrupt double-encoded
fields raise WatchlistError — no partial items from a failed parse. A
market with MISSING outcomePrices (new/closed) is tolerated with empty
prices; a CORRUPT one fails the payload.

Parsing uses stdlib ``json`` only (no new deps). Polymarket double-encodes
``outcomePrices``/``outcomes`` as JSON strings inside JSON.
Endpoint: https://gamma-api.polymarket.com/public-search?q=<query>
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from .url_safety import UnsafeUrlError, validate_http_url
from .watchlists import ConnectorItem, RunCaps, WatchlistError

Fetcher = Callable[[str, int], bytes]
Authorizer = Callable[[str], None]

_REQUEST_TIMEOUT = 30
_SEARCH = "https://gamma-api.polymarket.com/public-search?{params}"
_EVENT_LINK = "https://polymarket.com/event/{slug}"


def _deny_all(url: str) -> None:
    raise WatchlistError(
        f"egress not authorized for {url}: wire an explicit authorizer "
        "through the vault egress policy before enabling Polymarket watch runs"
    )


def _decode_prices(raw: object, *, market_id: str) -> tuple[str, str]:
    """Parse the double-encoded outcomePrices field (missing tolerated)."""
    if raw is None:
        return "", ""
    if not isinstance(raw, str):
        raise WatchlistError(
            f"corrupt double-encoded outcomePrices for market {market_id!r}"
        )
    try:
        prices = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WatchlistError(
            f"corrupt double-encoded outcomePrices for market {market_id!r}"
        ) from exc
    if not isinstance(prices, list) or len(prices) != 2:
        raise WatchlistError(
            f"corrupt double-encoded outcomePrices for market {market_id!r}"
        )
    return str(prices[0]), str(prices[1])


def parse_search(body: bytes) -> list[dict[str, str]]:
    """Parse one Gamma public-search payload into per-market dicts.

    Raises WatchlistError on malformed JSON, a non-object top level, a
    missing ``events`` key, or corrupt double-encoded fields — never returns
    partial results from a failed parse. An explicitly empty ``events`` list
    yields zero markets (truthful, not an error).
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WatchlistError(f"malformed search JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WatchlistError("malformed search JSON: top level is not an object")
    events = data.get("events")
    if events is None:
        raise WatchlistError("search payload missing events")
    if not isinstance(events, list):
        raise WatchlistError("search payload events is not a list")

    results: list[dict[str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            raise WatchlistError("search payload contains a non-object event")
        event_title = str(event.get("title") or "")
        event_slug = str(event.get("slug") or "")
        markets = event.get("markets") or []
        if not isinstance(markets, list):
            raise WatchlistError("search payload event markets is not a list")
        for market in markets:
            if not isinstance(market, dict):
                raise WatchlistError("search payload contains a non-object market")
            market_id = str(market.get("id") or "")
            yes_price, no_price = _decode_prices(
                market.get("outcomePrices"), market_id=market_id
            )
            results.append({
                "market_id": market_id,
                "question": str(market.get("question") or ""),
                "yes_price": yes_price,
                "no_price": no_price,
                "volume": str(market.get("volume") or ""),
                "liquidity": str(market.get("liquidity") or ""),
                "end_date": str(market.get("endDate") or ""),
                "event_title": event_title,
                "event_slug": event_slug,
                "link": _EVENT_LINK.format(slug=event_slug) if event_slug else "",
            })
    return results


class PolymarketConnector:
    """WatchConnector over Polymarket Gamma search, one item per market."""

    def __init__(
        self,
        queries: list[str],
        *,
        fetcher: Fetcher,
        authorize: Authorizer | None = None,
    ) -> None:
        self._queries = [str(q) for q in queries]
        self._fetcher = fetcher
        self._authorize = authorize or _deny_all

    def name(self) -> str:
        return "polymarket"

    def fetch(self, caps: RunCaps) -> tuple[list[ConnectorItem], bool]:
        items: list[ConnectorItem] = []
        truncated = False
        total_bytes = 0
        for raw_query in self._queries:
            query = raw_query.strip()
            if not query:
                raise WatchlistError(f"empty query term: {raw_query!r}")
            url = _SEARCH.format(params=urllib.parse.urlencode({"q": query}))
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

            for market in parse_search(body):
                if len(items) >= caps.max_items:
                    truncated = True
                    break
                items.append(ConnectorItem(
                    label=f"{len(items) + 1:03d}-{market['market_id'] or 'market'}.txt",
                    text=(
                        f"question: {market['question']}\n"
                        f"yes: {market['yes_price']}\n"
                        f"no: {market['no_price']}\n"
                        f"volume: {market['volume']}\n"
                        f"liquidity: {market['liquidity']}\n"
                        f"ends: {market['end_date']}\n"
                        f"event: {market['event_title']}\n"
                        f"link: {market['link']}\n"
                        f"search: {url}\n"
                    ),
                    source_path=Path(market["link"] or url),
                ))
            if truncated:
                break
        return items, truncated
