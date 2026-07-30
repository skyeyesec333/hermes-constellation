"""Tests for the egress-gated SEC EDGAR watchlist connector.

Mirrors the Http/RssConnector safety contract: default-deny authorization
before any network attempt, per-URL ledgering via the vault egress policy,
RunCaps truncation reported truthfully, and no partial items from a failed
parse. Fixtures are fictional companies on reserved endpoints.
"""

from pathlib import Path

import pytest

from constellation.edgar_connector import EdgarConnector, parse_submissions
from constellation.watchlists import RunCaps, WatchlistError


SUBMISSIONS_JSON = b"""{
  "cik": 320193,
  "name": "Acme Dynamics Inc",
  "filings": {
    "recent": {
      "accessionNumber": ["0000320193-26-000101", "0000320193-26-000099"],
      "form": ["8-K", "10-Q"],
      "filingDate": ["2026-07-25", "2026-07-20"],
      "primaryDocument": ["acme-8k_20260725.htm", "acme-10q_20260630.htm"],
      "primaryDocDescription": ["Current report", "Quarterly report"]
    }
  }
}
"""

SECOND_CIK_JSON = b"""{
  "cik": 1067983,
  "name": "NorthPeak Holdings LLC",
  "filings": {
    "recent": {
      "accessionNumber": ["0001067983-26-000007"],
      "form": ["13F-HR"],
      "filingDate": ["2026-07-22"],
      "primaryDocument": ["northpeak-13f_20260630.htm"],
      "primaryDocDescription": ["Quarterly holdings report"]
    }
  }
}
"""

THREE_FILINGS_JSON = b"""{
  "cik": 320193,
  "name": "Acme Dynamics Inc",
  "filings": {
    "recent": {
      "accessionNumber": ["0000320193-26-000101", "0000320193-26-000099", "0000320193-26-000088"],
      "form": ["8-K", "10-Q", "S-1"],
      "filingDate": ["2026-07-25", "2026-07-20", "2026-07-01"],
      "primaryDocument": ["a.htm", "b.htm", "c.htm"],
      "primaryDocDescription": ["x", "y", "z"]
    }
  }
}
"""

EXPECTED_URL = "https://data.sec.gov/submissions/CIK0000320193.json"


def _fetcher_ok(responses: dict[str, bytes], calls: list[str]):
    def _fetch(url: str, timeout: int) -> bytes:
        calls.append(url)
        return responses[url]
    return _fetch


def test_submissions_emit_one_item_per_filing() -> None:
    calls: list[str] = []
    connector = EdgarConnector(
        ["320193"],
        fetcher=_fetcher_ok({EXPECTED_URL: SUBMISSIONS_JSON}, calls),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=100_000))

    assert len(items) == 2
    assert truncated is False
    assert "Acme Dynamics Inc" in items[0].text
    assert "8-K" in items[0].text
    assert "2026-07-25" in items[0].text
    assert "Current report" in items[0].text
    assert "0000320193-26-000101" in items[0].text
    assert "10-Q" in items[1].text
    assert items[0].label != items[1].label
    assert calls == [EXPECTED_URL]  # CIK normalized to 10-digit zero-pad


def test_source_path_is_filing_url() -> None:
    connector = EdgarConnector(
        ["0000320193"],
        fetcher=_fetcher_ok({EXPECTED_URL: SUBMISSIONS_JSON}, []),
        authorize=lambda url: None,
    )

    items, _ = connector.fetch(RunCaps(max_items=10, max_bytes=100_000))

    assert items[0].source_path == Path(
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/acme-8k_20260725.htm"
    )
    assert items[1].source_path == Path(
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000099/acme-10q_20260630.htm"
    )


def test_item_cap_marks_truncated() -> None:
    connector = EdgarConnector(
        ["320193"],
        fetcher=_fetcher_ok({EXPECTED_URL: THREE_FILINGS_JSON}, []),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=2, max_bytes=100_000))

    assert len(items) == 2
    assert truncated is True


def test_byte_cap_marks_truncated() -> None:
    connector = EdgarConnector(
        ["320193"],
        fetcher=_fetcher_ok({EXPECTED_URL: SUBMISSIONS_JSON}, []),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=100))

    assert items == []
    assert truncated is True


def test_multiple_ciks_accumulate_with_unique_labels() -> None:
    calls: list[str] = []
    second_url = "https://data.sec.gov/submissions/CIK0001067983.json"
    connector = EdgarConnector(
        ["320193", "1067983"],
        fetcher=_fetcher_ok({EXPECTED_URL: SUBMISSIONS_JSON, second_url: SECOND_CIK_JSON}, calls),
        authorize=lambda url: None,
    )

    items, truncated = connector.fetch(RunCaps(max_items=10, max_bytes=500_000))

    assert len(items) == 3
    assert truncated is False
    assert len({item.label for item in items}) == 3
    assert calls == [EXPECTED_URL, second_url]


def test_invalid_cik_fails_closed() -> None:
    calls: list[str] = []
    connector = EdgarConnector(
        ["not-a-cik", "12345x"],
        fetcher=_fetcher_ok({}, calls),
        authorize=lambda url: None,
    )

    with pytest.raises(WatchlistError, match="CIK|cik"):
        connector.fetch(RunCaps())
    assert calls == []  # rejected before any network attempt


def test_default_authorize_denies_everything() -> None:
    calls: list[str] = []
    connector = EdgarConnector(
        ["320193"],
        fetcher=_fetcher_ok({EXPECTED_URL: SUBMISSIONS_JSON}, calls),
    )

    with pytest.raises(WatchlistError, match="denied|authorized|egress"):
        connector.fetch(RunCaps())
    assert calls == []  # denied before any network attempt


def test_authorize_runs_before_fetch_per_url() -> None:
    order: list[str] = []

    def _authorize(url: str) -> None:
        order.append(f"auth:{url}")

    def _fetch(url: str, timeout: int) -> bytes:
        order.append(f"fetch:{url}")
        return SUBMISSIONS_JSON

    connector = EdgarConnector(["320193"], fetcher=_fetch, authorize=_authorize)
    connector.fetch(RunCaps(max_items=10, max_bytes=100_000))

    assert order == [f"auth:{EXPECTED_URL}", f"fetch:{EXPECTED_URL}"]


def test_malformed_json_fails_truthfully() -> None:
    calls: list[str] = []
    connector = EdgarConnector(
        ["320193"],
        fetcher=_fetcher_ok({EXPECTED_URL: b'{"cik": 320193, "filings": {'}, calls),
        authorize=lambda url: None,
    )

    with pytest.raises(WatchlistError, match="malformed|JSON|parse"):
        connector.fetch(RunCaps())
    assert calls == [EXPECTED_URL]  # fetch happened; parse failed, no partial items


def test_missing_recent_filings_fails_truthfully() -> None:
    connector = EdgarConnector(
        ["320193"],
        fetcher=_fetcher_ok({EXPECTED_URL: b'{"cik": 320193, "name": "Acme"}'}, []),
        authorize=lambda url: None,
    )

    with pytest.raises(WatchlistError, match="recent|filings|missing"):
        connector.fetch(RunCaps())


def test_parse_submissions_empty_recent_yields_no_filings() -> None:
    filings = parse_submissions(
        b'{"cik": 1, "name": "Empty Corp", "filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "primaryDocument": [], "primaryDocDescription": []}}}'
    )
    assert filings == []
