"""Tests for the egress-gated Polymarket Gamma connector.

Mirrors the EDGAR connector contract: authorization BEFORE any network
attempt (default deny), URL construction from validated query terms only,
RunCaps truncation reported truthfully, malformed payloads fail closed with
zero partial items. One ConnectorItem PER MARKET so the snapshot diff sees
per-market materiality (price movement = changed item).
"""

import json

import pytest

from constellation.polymarket_connector import (
    PolymarketConnector,
    parse_search,
)
from constellation.watchlists import RunCaps, WatchlistError


def _payload() -> bytes:
    return json.dumps({
        "events": [
            {
                "id": "12345",
                "title": "AI regulation 2026",
                "slug": "ai-regulation-2026",
                "volume": 2500000.0,
                "markets": [
                    {
                        "id": "m1",
                        "question": "Will the EU pass a new AI liability directive in 2026?",
                        "outcomePrices": "[\"0.62\", \"0.38\"]",
                        "outcomes": "[\"Yes\", \"No\"]",
                        "volume": 1400000,
                        "liquidity": 210000,
                        "endDate": "2026-12-31T00:00:00Z",
                    },
                    {
                        "id": "m2",
                        "question": "Will the US pass a federal AI safety bill in 2026?",
                        "outcomePrices": "[\"0.21\", \"0.79\"]",
                        "outcomes": "[\"Yes\", \"No\"]",
                        "volume": 1100000,
                        "liquidity": 90000,
                        "endDate": "2026-12-31T00:00:00Z",
                    },
                ],
            }
        ],
        "pagination": {"hasMore": False, "totalResults": 1},
    }).encode()


def test_parse_search_emits_one_dict_per_market() -> None:
    markets = parse_search(_payload())

    assert len(markets) == 2
    first = markets[0]
    assert first["question"].startswith("Will the EU")
    assert first["yes_price"] == "0.62"
    assert first["no_price"] == "0.38"
    assert first["event_title"] == "AI regulation 2026"
    assert first["event_slug"] == "ai-regulation-2026"
    assert first["end_date"] == "2026-12-31T00:00:00Z"
    assert first["link"] == "https://polymarket.com/event/ai-regulation-2026"


def test_parse_search_malformed_json_fails_closed() -> None:
    with pytest.raises(WatchlistError, match="malformed search JSON"):
        parse_search(b"{not json")


def test_parse_search_non_object_fails_closed() -> None:
    with pytest.raises(WatchlistError, match="top level is not an object"):
        parse_search(b"[1, 2]")


def test_parse_search_missing_events_fails_closed() -> None:
    with pytest.raises(WatchlistError, match="missing events"):
        parse_search(b'{"pagination": {}}')


def test_parse_search_empty_events_is_truthful_zero() -> None:
    assert parse_search(b'{"events": []}') == []


def test_parse_search_corrupt_double_encoding_fails_closed() -> None:
    payload = json.loads(_payload())
    payload["events"][0]["markets"][0]["outcomePrices"] = "not-json{"
    with pytest.raises(WatchlistError, match="double-encoded"):
        parse_search(json.dumps(payload).encode())


def test_default_authorizer_denies_before_fetch() -> None:
    called = []
    connector = PolymarketConnector(
        ["ai regulation"],
        fetcher=lambda url, timeout: called.append(url) or _payload(),
    )
    with pytest.raises(WatchlistError, match="egress not authorized"):
        connector.fetch(RunCaps(max_items=10, max_bytes=1_000_000))
    assert called == []  # zero network attempts


def test_authorize_runs_before_fetch_per_query() -> None:
    order: list[str] = []

    def authorize(url: str) -> None:
        order.append(f"auth:{url}")

    def fetch(url: str, timeout: int) -> bytes:
        order.append(f"fetch:{url}")
        return _payload()

    connector = PolymarketConnector(["ai regulation"], fetcher=fetch, authorize=authorize)
    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=1_000_000))

    assert truncated is False
    assert len(items) == 2
    assert order[0].startswith("auth:")
    assert order[1].startswith("fetch:")
    assert "ai%20regulation" in order[0] or "ai+regulation" in order[0]


def test_item_text_carries_market_materials() -> None:
    connector = PolymarketConnector(
        ["ai"], fetcher=lambda url, timeout: _payload(), authorize=lambda url: None
    )
    items, _ = connector.fetch(RunCaps(max_items=10, max_bytes=1_000_000))

    text = items[0].text
    assert "Will the EU" in text
    assert "yes: 0.62" in text
    assert "no: 0.38" in text
    assert "volume: 1400000" in text
    assert "https://polymarket.com/event/ai-regulation-2026" in text


def test_max_items_truncation_reported() -> None:
    connector = PolymarketConnector(
        ["ai"], fetcher=lambda url, timeout: _payload(), authorize=lambda url: None
    )
    items, truncated = connector.fetch(RunCaps(max_items=1, max_bytes=1_000_000))

    assert len(items) == 1
    assert truncated is True


def test_max_bytes_truncation_reported() -> None:
    connector = PolymarketConnector(
        ["ai", "second query"],
        fetcher=lambda url, timeout: _payload(),
        authorize=lambda url: None,
    )
    items, truncated = connector.fetch(RunCaps(max_items=100, max_bytes=len(_payload())))

    assert len(items) == 2  # second query's payload never fits
    assert truncated is True


def test_empty_query_rejected() -> None:
    connector = PolymarketConnector(
        ["   "], fetcher=lambda url, timeout: _payload(), authorize=lambda url: None
    )
    with pytest.raises(WatchlistError, match="empty query"):
        connector.fetch(RunCaps(max_items=10, max_bytes=1_000_000))


def test_missing_prices_tolerated_for_new_markets() -> None:
    payload = json.loads(_payload())
    del payload["events"][0]["markets"][0]["outcomePrices"]
    markets = parse_search(json.dumps(payload).encode())

    assert markets[0]["yes_price"] == ""
    assert markets[0]["no_price"] == ""
