"""Tests for the egress-gated HTTP watchlist connector."""

import pytest

from constellation.http_connector import HttpConnector
from constellation.watchlists import RunCaps, WatchlistError


def _fetcher_ok(responses: dict[str, bytes], calls: list[str]):
    def _fetch(url: str, timeout: int) -> bytes:
        calls.append(url)
        return responses[url]
    return _fetch


def test_fetches_declared_urls_with_caps() -> None:
    calls: list[str] = []
    connector = HttpConnector(
        ["https://example.test/a", "https://example.test/b"],
        fetcher=_fetcher_ok({
            "https://example.test/a": b"alpha content",
            "https://example.test/b": b"beta content",
        }, calls),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=10_000))

    assert len(items) == 2
    assert truncated is False
    assert items[0].text == "alpha content"
    assert calls == ["https://example.test/a", "https://example.test/b"]


def test_item_cap_marks_truncated() -> None:
    connector = HttpConnector(
        ["https://example.test/a", "https://example.test/b", "https://example.test/c"],
        fetcher=_fetcher_ok({u: b"x" for u in (
            "https://example.test/a", "https://example.test/b", "https://example.test/c")}, []),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=2, max_bytes=10_000))

    assert len(items) == 2
    assert truncated is True


def test_byte_cap_marks_truncated() -> None:
    connector = HttpConnector(
        ["https://example.test/big"],
        fetcher=_fetcher_ok({"https://example.test/big": b"x" * 5000}, []),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=100))

    assert items == []
    assert truncated is True


def test_private_address_url_fails_closed() -> None:
    connector = HttpConnector(
        ["http://localhost/admin", "http://ip6-localhost/internal"],
        fetcher=_fetcher_ok({}, []),
        authorize=lambda url: None,
    )

    with pytest.raises(WatchlistError, match="unsafe|blocked|private|URL"):
        connector.fetch(RunCaps())


def test_default_authorize_denies_everything() -> None:
    calls: list[str] = []
    connector = HttpConnector(
        ["https://example.test/a"],
        fetcher=_fetcher_ok({"https://example.test/a": b"x"}, calls),
    )

    with pytest.raises(WatchlistError, match="denied|authorized|egress"):
        connector.fetch(RunCaps())
    assert calls == []  # denied before any network attempt


def test_non_http_scheme_fails_closed() -> None:
    connector = HttpConnector(
        ["file:///etc/passwd", "ftp://example.test/x"],
        fetcher=_fetcher_ok({}, []),
        authorize=lambda url: None,
    )

    with pytest.raises(WatchlistError):
        connector.fetch(RunCaps())
