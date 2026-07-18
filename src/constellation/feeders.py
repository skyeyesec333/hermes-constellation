"""External intelligence feeders — GDELT, SEC EDGAR, Polymarket.

Phase 14: query external APIs, stage results as source-items and claims.
Each feeder is self-contained and fail-safe — API errors produce graceful
messages, not crashes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .claim import stage_claim
from .models import generate_ulid as _gen_ulid


class FeederError(RuntimeError):
    """Raised when an external feeder fails."""


# ── GDELT ──────────────────────────────────────────────────────────

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"

_GDLET_MODE_MAP = {
    "artlist": "artlist",
    "timelinevol": "timelinevol",
    "tonechart": "tonechart",
}


def query_gdelt(
    query: str,
    *,
    mode: str = "artlist",
    max_records: int = 10,
    timespan: str = "7d",
    timeout: int = 30,
) -> dict[str, object]:
    """Query the GDELT Project API for news articles matching a query.

    Args:
        query: Search terms (supports AND, OR, phrases in quotes)
        mode: 'artlist' (articles), 'timelinevol' (volume), 'tonechart' (sentiment)
        max_records: Max articles to return
        timespan: Time window ('24h', '7d', '30d', '90d', '1y')
        timeout: HTTP timeout in seconds

    Returns:
        Dict with status, articles list, and raw response metadata
    """
    if mode not in _GDLET_MODE_MAP:
        raise FeederError(f"unsupported GDELT mode: {mode}")

    params = {
        "query": query,
        "mode": mode,
        "maxrecords": str(max_records),
        "timespan": timespan,
        "format": "json",
    }
    url = f"{GDELT_API}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise FeederError(f"GDELT API unreachable: {exc}") from exc

    # GDELT sometimes returns empty responses
    if not body.strip():
        return {"status": "empty", "articles": [], "query": query}

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FeederError(f"GDELT returned invalid JSON: {exc}") from exc

    articles = data.get("articles", []) if isinstance(data, dict) else []
    return {
        "status": "ok" if articles else "no_results",
        "articles": articles[:max_records],
        "total": len(articles),
        "query": query,
    }


def enrich_entity_gdelt(
    vault: Path | str,
    entity_name: str,
    *,
    subject_id: str | None = None,
    max_claims: int = 10,
) -> dict[str, object]:
    """Query GDELT for an entity and stage news mentions as claims.

    Returns stats: articles_found, claims_staged, run_id.
    """
    vault = Path(vault).absolute()
    gdelt_result = query_gdelt(f'"{entity_name}"', mode="artlist", max_records=10)

    articles = gdelt_result.get("articles", [])
    if not articles:
        return {"status": "no_results", "articles_found": 0, "claims_staged": 0}

    entity_id = subject_id or _gen_ulid()
    staged = 0
    now = datetime.now(UTC)

    for art in articles[:max_claims]:
        if not isinstance(art, dict):
            continue
        title = str(art.get("title", ""))[:200]
        url = str(art.get("url", ""))
        tone = art.get("tone", {})

        if not title:
            continue

        tone_str = ""
        if isinstance(tone, dict):
            avg_tone = tone.get("tone", 0)
            tone_str = f" (tone: {avg_tone})" if avg_tone else ""

        try:
            stage_claim(
                vault,
                subject_id=entity_id,
                predicate="mentioned_in_news",
                object_literal=f"{title}{tone_str}",
                source_ids=[],
                evidence_excerpt=url,
                claim_status="source-claimed",
                confidence=0.70,
                observed_at=now,
            )
            staged += 1
        except Exception:
            continue

    return {
        "status": "complete" if staged else "no_claims",
        "articles_found": len(articles),
        "claims_staged": staged,
        "entity_name": entity_name,
    }


# ── SEC EDGAR ───────────────────────────────────────────────────────

EDGAR_API = "https://efts.sec.gov/LATEST/search-index"

_COMPANY_KEYWORDS = {
    "10-K": "annual report",
    "8-K": "material event",
    "13F-HR": "institutional holdings",
    "4": "insider transaction",
    "SC 13G": "beneficial ownership",
}


def query_edgar(
    company_name: str,
    *,
    form_types: list[str] | None = None,
    max_results: int = 10,
    timeout: int = 30,
) -> dict[str, object]:
    """Query SEC EDGAR for company filings.

    Returns dict with filings list.
    """
    types = form_types or ["10-K", "8-K"]
    query_parts = [f'companyName:"{company_name}"']
    for ft in types:
        query_parts.append(f'formType:"{ft}"')

    q = " AND ".join(query_parts)
    params = {
        "q": q,
        "sort": "filedAt",
        "order": "desc",
        "pageSize": str(max_results),
    }
    url = f"{EDGAR_API}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": "Constellation/0.2 (" + "contact" + "@" + "example.test" + ")"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise FeederError(f"EDGAR API unreachable: {exc}") from exc

    hits = body.get("hits", {}).get("hits", [])
    filings = []
    for hit in hits:
        source = hit.get("_source", {})
        filings.append({
            "company": source.get("companyName", company_name),
            "form": source.get("formType", ""),
            "filed_at": source.get("filedAt", ""),
            "description": source.get("fileDescription", ""),
            "accession": source.get("accessionNumber", ""),
        })

    return {"status": "ok" if filings else "no_results", "filings": filings, "query": company_name}


def enrich_entity_edgar(
    vault: Path | str,
    company_name: str,
    *,
    subject_id: str | None = None,
    ticker: str | None = None,
    max_claims: int = 10,
) -> dict[str, object]:
    """Query SEC EDGAR and stage filing info as claims."""
    vault = Path(vault).absolute()
    edgar_result = query_edgar(company_name, max_results=10)

    filings = edgar_result.get("filings", [])
    if not filings:
        return {"status": "no_results", "filings_found": 0, "claims_staged": 0}

    entity_id = subject_id or _gen_ulid()
    staged = 0
    now = datetime.now(UTC)

    for filing in filings[:max_claims]:
        form = filing.get("form", "")
        desc = filing.get("description", "")[:200]
        filed = filing.get("filed_at", "")
        if not form:
            continue

        form_label = _COMPANY_KEYWORDS.get(form, form)
        try:
            stage_claim(
                vault,
                subject_id=entity_id,
                predicate=f"filed_{form.lower().replace(' ', '_').replace('-', '_')}",
                object_literal=f"{form_label}: {desc} (filed {filed})",
                source_ids=[],
                evidence_excerpt=f"SEC EDGAR {form} filing {filing.get('accession', '')}",
                claim_status="source-claimed",
                confidence=0.95,
                observed_at=now,
            )
            staged += 1
        except Exception:
            continue

    return {
        "status": "complete" if staged else "no_claims",
        "filings_found": len(filings),
        "claims_staged": staged,
        "company": company_name,
    }


# ── Polymarket ──────────────────────────────────────────────────────

POLYMARKET_API = "https://gamma-api.polymarket.com"


def query_polymarket(
    query: str,
    *,
    limit: int = 10,
    timeout: int = 30,
) -> dict[str, object]:
    """Query Polymarket for prediction markets matching a query."""
    params = {
        "query": query,
        "limit": str(limit),
    }
    url = f"{POLYMARKET_API}/markets?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise FeederError(f"Polymarket API unreachable: {exc}") from exc

    if not isinstance(body, list):
        return {"status": "no_results", "markets": [], "query": query}

    markets = []
    for m in body[:limit]:
        if not isinstance(m, dict):
            continue
        markets.append({
            "title": m.get("title", ""),
            "question": m.get("question", ""),
            "outcome_prices": m.get("outcomePrices", []),
            "volume": m.get("volume", 0),
            "closed": m.get("closed", False),
        })

    return {"status": "ok" if markets else "no_results", "markets": markets, "query": query}


def enrich_entity_polymarket(
    vault: Path | str,
    query: str,
    *,
    subject_id: str | None = None,
    max_claims: int = 10,
) -> dict[str, object]:
    """Query Polymarket and stage prediction data as claims."""
    vault = Path(vault).absolute()
    pm_result = query_polymarket(query, limit=10)

    markets = pm_result.get("markets", [])
    if not markets:
        return {"status": "no_results", "markets_found": 0, "claims_staged": 0}

    entity_id = subject_id or _gen_ulid()
    staged = 0
    now = datetime.now(UTC)

    for market in markets[:max_claims]:
        title = market.get("title", "")[:200]
        prices = market.get("outcome_prices", [])
        volume = market.get("volume", 0)
        if not title:
            continue

        price_str = ", ".join(str(p) for p in prices[:3]) if prices else "no prices"
        try:
            stage_claim(
                vault,
                subject_id=entity_id,
                predicate="prediction_market",
                object_literal=f"{title} (prices: {price_str}, volume: {volume})",
                source_ids=[],
                evidence_excerpt="Polymarket market data",
                claim_status="source-claimed",
                confidence=0.70,
                observed_at=now,
            )
            staged += 1
        except Exception:
            continue

    return {
        "status": "complete" if staged else "no_claims",
        "markets_found": len(markets),
        "claims_staged": staged,
        "query": query,
    }
