"""Tests for the egress-gated RSS/Atom watchlist connector."""

from pathlib import Path

import pytest

from constellation.rss_connector import RssConnector
from constellation.watchlists import RunCaps, WatchlistError


RSS20_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example News</title>
    <link>https://example.test/</link>
    <description>Fixture feed</description>
    <item>
      <title>Acme closes Series B</title>
      <link>https://example.test/acme-series-b</link>
      <description>Acme raised $40M led by NorthPeak.</description>
      <pubDate>Mon, 27 Jul 2026 09:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Acme hires new CTO</title>
      <link>https://example.test/acme-cto</link>
      <description>Former Globex VP joins as CTO.</description>
      <pubDate>Tue, 28 Jul 2026 12:30:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <link href="https://example.test/"/>
  <updated>2026-07-28T12:00:00Z</updated>
  <entry>
    <title>Beta Corp opens Berlin office</title>
    <link rel="alternate" href="https://example.test/beta-berlin"/>
    <id>urn:uuid:1</id>
    <published>2026-07-26T08:00:00Z</published>
    <updated>2026-07-26T08:00:00Z</updated>
    <summary>Expansion into the DACH market.</summary>
  </entry>
  <entry>
    <title>Beta Corp partners with Delta GmbH</title>
    <link rel="alternate" href="https://example.test/beta-delta"/>
    <id>urn:uuid:2</id>
    <published>2026-07-27T10:15:00Z</published>
    <updated>2026-07-27T10:15:00Z</updated>
    <summary>Joint go-to-market agreement.</summary>
  </entry>
</feed>
"""

RSS20_THREE_ITEMS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Three</title>
    <item><title>One</title><link>https://example.test/1</link>
      <description>a</description><pubDate>d1</pubDate></item>
    <item><title>Two</title><link>https://example.test/2</link>
      <description>b</description><pubDate>d2</pubDate></item>
    <item><title>Three</title><link>https://example.test/3</link>
      <description>c</description><pubDate>d3</pubDate></item>
  </channel>
</rss>
"""


def _fetcher_ok(responses: dict[str, bytes], calls: list[str]):
    def _fetch(url: str, timeout: int) -> bytes:
        calls.append(url)
        return responses[url]
    return _fetch


def test_rss20_emits_one_item_per_entry() -> None:
    calls: list[str] = []
    connector = RssConnector(
        ["https://example.test/feed.xml"],
        fetcher=_fetcher_ok({"https://example.test/feed.xml": RSS20_FEED}, calls),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=100_000))

    assert len(items) == 2
    assert truncated is False
    assert "Acme closes Series B" in items[0].text
    assert "NorthPeak" in items[0].text
    assert "https://example.test/acme-series-b" in items[0].text
    assert "2026" in items[0].text
    assert items[0].source_path == Path("https://example.test/acme-series-b")
    assert "Acme hires new CTO" in items[1].text
    assert items[0].label != items[1].label
    assert calls == ["https://example.test/feed.xml"]


def test_atom_emits_one_item_per_entry() -> None:
    connector = RssConnector(
        ["https://example.test/atom.xml"],
        fetcher=_fetcher_ok({"https://example.test/atom.xml": ATOM_FEED}, []),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=100_000))

    assert len(items) == 2
    assert truncated is False
    assert "Beta Corp opens Berlin office" in items[0].text
    assert "DACH" in items[0].text
    assert items[0].source_path == Path("https://example.test/beta-berlin")
    assert "Beta Corp partners with Delta GmbH" in items[1].text


def test_item_cap_marks_truncated() -> None:
    connector = RssConnector(
        ["https://example.test/feed.xml"],
        fetcher=_fetcher_ok({"https://example.test/feed.xml": RSS20_THREE_ITEMS}, []),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=2, max_bytes=100_000))

    assert len(items) == 2
    assert truncated is True


def test_byte_cap_marks_truncated() -> None:
    connector = RssConnector(
        ["https://example.test/feed.xml"],
        fetcher=_fetcher_ok({"https://example.test/feed.xml": RSS20_FEED}, []),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=100))

    assert items == []
    assert truncated is True


def test_multiple_feeds_accumulate_with_unique_labels() -> None:
    connector = RssConnector(
        ["https://example.test/rss.xml", "https://example.test/atom.xml"],
        fetcher=_fetcher_ok({
            "https://example.test/rss.xml": RSS20_FEED,
            "https://example.test/atom.xml": ATOM_FEED,
        }, []),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=500_000))

    assert len(items) == 4
    assert truncated is False
    assert len({item.label for item in items}) == 4


def test_private_address_url_fails_closed() -> None:
    connector = RssConnector(
        ["http://localhost/feed.xml", "http://ip6-localhost/feed.xml"],
        fetcher=_fetcher_ok({}, []),
        authorize=lambda url: None,
    )

    with pytest.raises(WatchlistError, match="unsafe|blocked|private|URL"):
        connector.fetch(RunCaps())


def test_default_authorize_denies_everything() -> None:
    calls: list[str] = []
    connector = RssConnector(
        ["https://example.test/feed.xml"],
        fetcher=_fetcher_ok({"https://example.test/feed.xml": RSS20_FEED}, calls),
    )

    with pytest.raises(WatchlistError, match="denied|authorized|egress"):
        connector.fetch(RunCaps())
    assert calls == []  # denied before any network attempt


def test_non_http_scheme_fails_closed() -> None:
    connector = RssConnector(
        ["file:///etc/passwd", "ftp://example.test/feed.xml"],
        fetcher=_fetcher_ok({}, []),
        authorize=lambda url: None,
    )

    with pytest.raises(WatchlistError):
        connector.fetch(RunCaps())


def test_malformed_xml_fails_truthfully() -> None:
    calls: list[str] = []
    connector = RssConnector(
        ["https://example.test/broken.xml"],
        fetcher=_fetcher_ok(
            {"https://example.test/broken.xml": b"<rss><channel><item><title>oops"}, calls),
        authorize=lambda url: None,
    )

    with pytest.raises(WatchlistError, match="malformed|parse|XML"):
        connector.fetch(RunCaps())
    assert calls == ["https://example.test/broken.xml"]  # fetch happened; parse failed, no partial items


def test_unsupported_root_fails_truthfully() -> None:
    connector = RssConnector(
        ["https://example.test/notafeed.xml"],
        fetcher=_fetcher_ok(
            {"https://example.test/notafeed.xml": b"<html><body>not a feed</body></html>"}, []),
        authorize=lambda url: None,
    )

    with pytest.raises(WatchlistError, match="unsupported|feed|root"):
        connector.fetch(RunCaps())
