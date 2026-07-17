"""Research runner: escalates through web extraction tools for constellation inquiries."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from .models import Inquiry, Sensitivity
from .search_adapter import SearchAdapterError, search_web


class ResearchRunnerError(RuntimeError):
    """Raised when the research pipeline fails."""


def _extract_page(url: str, *, sensitivity: Sensitivity, timeout: int = 30) -> dict[str, object] | None:
    """Try extracting a page through the escalation ladder. Returns markdown or None."""
    # Layer 1: Firecrawl (fast, clean markdown)
    try:
        from .firecrawl_adapter import extract_page as firecrawl_extract

        result = firecrawl_extract(url, sensitivity=sensitivity, timeout=timeout)
        if result and result.get("markdown"):
            result["method"] = "firecrawl"
            return result
    except (ImportError, SearchAdapterError):
        pass

    # Layer 2: Crawl4AI (structured bulk extraction, BFS/DFS deep crawl)
    try:
        from crawl4ai import AsyncWebCrawler
        import asyncio

        async def _crawl():
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url)
                return result

        crawl_result = asyncio.run(_crawl())
        if crawl_result and crawl_result.markdown:
            return {
                "title": getattr(crawl_result, "title", "") or "",
                "url": url,
                "markdown": crawl_result.markdown[:50_000],
                "method": "crawl4ai",
                "extracted_at": datetime.now().astimezone().isoformat(),
            }
    except (ImportError, Exception):
        pass

    # Layer 3: Scrapling (undetectable, anti-bot bypass)
    try:
        import scrapling

        # Scrapling fetches with stealth browser-like behavior
        page = scrapling.fetch(url, timeout=timeout)
        if page and page.text:
            return {
                "title": getattr(page, "title", "") or "",
                "url": url,
                "markdown": getattr(page, "markdown", None) or page.text,
                "method": "scrapling",
                "extracted_at": datetime.now().astimezone().isoformat(),
            }
    except (ImportError, Exception):
        pass

    # Layer 4: Browser Use (autonomous browser agent — heaviest, last resort)
    try:
        from browser_use import Agent
        import asyncio as _asyncio

        async def _browse():
            agent = Agent(task=f"Extract the main content from {url} as clean markdown text. Return only the extracted content.")
            result = await agent.run()
            return result

        bu_result = _asyncio.run(_browse())
        if bu_result and hasattr(bu_result, 'final_result'):
            return {
                "title": "",
                "url": url,
                "markdown": str(bu_result.final_result())[:50_000],
                "method": "browser-use",
                "extracted_at": datetime.now().astimezone().isoformat(),
            }
    except (ImportError, Exception):
        pass

    return None


def _web_extract_text(url: str, *, timeout: int = 30) -> str | None:
    """Fast text extraction via stdlib as last resort."""
    import urllib.request
    import re as _re

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")[:500_000]
        # Crude text extraction
        text = _re.sub(r"<script[^>]*>.*?</script>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
        text = _re.sub(r"<style[^>]*>.*?</style>", "", text, flags=_re.DOTALL | _re.IGNORECASE)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"\s+", " ", text).strip()
        return text[:50_000] if text else None
    except Exception:
        return None


def run_inquiry(
    vault: Path | str,
    inquiry: Inquiry,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    max_pages: int = 5,
) -> dict[str, object]:
    """Execute a bounded web research inquiry.

    Returns a research manifest with discovered sources, extracted pages,
    and a summary of findings. Does NOT auto-promote claims — staged only.
    """
    # Guard: confidential/restricted never leaves the machine
    if sensitivity in {Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED}:
        raise ResearchRunnerError(
            f"web research blocked: sensitivity {sensitivity.value} requires local-only processing"
        )

    results: list[dict[str, object]] = []
    sources_discovered = 0
    sources_extracted = 0
    queries_used = 0

    # Step 1: Discover via SearXNG
    search_results = search_web(
        inquiry.question,
        sensitivity=sensitivity,
        limit=min(inquiry.max_search_queries * 2, 10),
    )

    queries_used += 1
    urls_to_extract: list[str] = []
    for sr in search_results:
        url = str(sr.get("url", ""))
        if url and url.startswith("http") and url not in urls_to_extract:
            urls_to_extract.append(url)
            sources_discovered += 1
        if len(urls_to_extract) >= max_pages:
            break

    # Step 2: Extract each URL through the escalation ladder
    for url in urls_to_extract:
        if sources_extracted >= inquiry.max_unique_sources:
            break
        page = _extract_page(url, sensitivity=sensitivity)
        if page:
            sources_extracted += 1
            results.append(
                {
                    "url": url,
                    "title": page.get("title", ""),
                    "method": page.get("method", "unknown"),
                    "markdown_preview": str(page.get("markdown", ""))[:2000],
                    "extracted_at": page.get("extracted_at", ""),
                }
            )
        else:
            # Last resort: raw text extraction
            text = _web_extract_text(url)
            if text:
                sources_extracted += 1
                results.append(
                    {
                        "url": url,
                        "title": "",
                        "method": "raw",
                        "markdown_preview": text[:2000],
                        "extracted_at": datetime.now().astimezone().isoformat(),
                    }
                )

    return {
        "status": "complete" if results else "no_results",
        "question": inquiry.question,
        "queries_used": queries_used,
        "sources_discovered": sources_discovered,
        "sources_extracted": sources_extracted,
        "results": results,
        "completed_at": datetime.now().astimezone().isoformat(),
    }
