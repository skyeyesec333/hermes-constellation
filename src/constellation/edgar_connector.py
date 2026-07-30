"""Egress-gated SEC EDGAR connector for watchlist runs.

Safety contract (identical to HttpConnector / RssConnector):
- authorization is checked BEFORE any network attempt, and the default
  authorizer denies everything (fail closed — wire egress explicitly);
- request URLs are constructed from validated CIK numbers only (digits,
  zero-padded to 10) and still pass ``url_safety.validate_http_url``
  before fetching;
- explicit RunCaps bound items and bytes; truncation is reported truthfully.

An EDGAR submissions payload emits ONE ConnectorItem PER RECENT FILING
(form + accession + filing date + primary document) so the snapshot diff
sees per-filing materiality — a new 8-K appears as a new/changed item.
Malformed JSON or a payload missing ``filings.recent`` raises
WatchlistError — no partial items are emitted from a failed parse.

Parsing uses stdlib ``json`` only (no new deps).
Endpoint: https://data.sec.gov/submissions/CIK{10-digit}.json
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .url_safety import UnsafeUrlError, validate_http_url
from .watchlists import ConnectorItem, RunCaps, WatchlistError

Fetcher = Callable[[str, int], bytes]
Authorizer = Callable[[str], None]

_REQUEST_TIMEOUT = 30
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
_FILING_DOC = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_flat}/{doc}"


def _deny_all(url: str) -> None:
    raise WatchlistError(
        f"egress not authorized for {url}: wire an explicit authorizer "
        "through the vault egress policy before enabling EDGAR watch runs"
    )


def _normalize_cik(cik: str) -> str:
    digits = str(cik).strip()
    if not digits.isdigit() or not digits:
        raise WatchlistError(f"invalid CIK (digits only): {cik!r}")
    return digits.zfill(10)


def parse_submissions(body: bytes) -> list[dict[str, str]]:
    """Parse one EDGAR submissions payload into recent-filing dicts.

    Raises WatchlistError on malformed JSON or a payload without
    ``filings.recent`` — never returns partial results from a failed parse.
    An explicitly empty ``recent`` set yields zero filings (truthful, not
    an error).
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WatchlistError(f"malformed submissions JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WatchlistError("malformed submissions JSON: top level is not an object")
    filings = data.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        raise WatchlistError("submissions payload missing filings.recent")

    accessions = recent.get("accessionNumber") or []
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    docs = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []
    if not (len(accessions) == len(forms) == len(dates) == len(docs) == len(descriptions)):
        raise WatchlistError("filings.recent column lengths diverge — refusing partial parse")

    cik_int = str(int(data.get("cik") or 0))
    company = str(data.get("name") or "")
    results: list[dict[str, str]] = []
    for accession, form, date, doc, description in zip(
        accessions, forms, dates, docs, descriptions, strict=True
    ):
        accession_flat = str(accession).replace("-", "")
        results.append({
            "company": company,
            "cik": cik_int,
            "accession": str(accession),
            "form": str(form),
            "filing_date": str(date),
            "primary_document": str(doc),
            "description": str(description),
            "link": _FILING_DOC.format(
                cik_int=cik_int, accession_flat=accession_flat, doc=doc
            ),
        })
    return results


class EdgarConnector:
    """WatchConnector over SEC EDGAR submissions, one item per recent filing."""

    def __init__(
        self,
        ciks: list[str],
        *,
        fetcher: Fetcher,
        authorize: Authorizer | None = None,
    ) -> None:
        self._ciks = [str(c) for c in ciks]  # normalized per fetch, fail-closed
        self._fetcher = fetcher
        self._authorize = authorize or _deny_all

    def name(self) -> str:
        return "edgar"

    def fetch(self, caps: RunCaps) -> tuple[list[ConnectorItem], bool]:
        items: list[ConnectorItem] = []
        truncated = False
        total_bytes = 0
        for raw_cik in self._ciks:
            cik = _normalize_cik(raw_cik)  # before any URL construction
            url = _SUBMISSIONS.format(cik=cik)
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

            for filing in parse_submissions(body):
                if len(items) >= caps.max_items:
                    truncated = True
                    break
                items.append(ConnectorItem(
                    label=f"{len(items) + 1:03d}-{filing['form']}-{filing['accession']}.txt",
                    text=(
                        f"company: {filing['company']}\n"
                        f"cik: {filing['cik']}\n"
                        f"form: {filing['form']}\n"
                        f"accession: {filing['accession']}\n"
                        f"filed: {filing['filing_date']}\n"
                        f"description: {filing['description']}\n"
                        f"link: {filing['link']}\n"
                        f"submissions: {url}\n"
                    ),
                    source_path=Path(filing["link"]),
                ))
            if truncated:
                break
        return items, truncated
