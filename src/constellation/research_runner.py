"""Research runner: escalates through web extraction tools for constellation inquiries.

Every network adapter is gated through the vault egress policy. The runner
records budget consumption and generates an immutable research receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, UTC
from pathlib import Path

from .egress import EgressDenied, EgressRequest, require_egress
from .firecrawl_adapter import FirecrawlAdapterError
from .models import Inquiry, ResearchTerminalState, Sensitivity
from .research import BudgetExhausted, ResearchBudget, ResearchLedger
from .search_adapter import SearchAdapterError, brave_search, exa_search, search_web
from .storage import atomic_write_text, safe_relative_path
from .url_safety import UnsafeUrlError, validate_http_url

_ADAPTER_PROVIDERS: dict[str, tuple[str, str]] = {
    "firecrawl": ("firecrawl", "firecrawl-local"),
    "crawl4ai": ("crawl4ai", "crawl4ai-local"),
    "scrapling": ("scrapling", "scrapling-local"),
    "browser-use": ("browser-use", "browser-use-local"),
    "exa": ("exa", "exa-api"),
    "brave": ("brave", "brave-api"),
}

# The installed self-hosted Firecrawl build checks the initial target, every
# browser request/redirect, and each fetch connection against resolved private
# addresses. The other extraction libraries have no equivalent verified
# connection boundary, so explicit operator-supplied URLs must not reach them.
_EXPLICIT_URL_ADAPTERS = frozenset({"firecrawl"})
_MAX_EXTRACTED_TEXT_CHARS = 50_000
_MAX_EXTRACTED_CONTEXT_BYTES = _MAX_EXTRACTED_TEXT_CHARS * 4

_RELEVANCE_STOPWORDS = frozenset(
    {
        "about",
        "and",
        "are",
        "at",
        "commercial",
        "current",
        "does",
        "evidenced",
        "for",
        "from",
        "hold",
        "holds",
        "how",
        "into",
        "mandate",
        "public",
        "role",
        "scope",
        "that",
        "the",
        "their",
        "this",
        "what",
        "which",
        "who",
        "with",
    }
)


def _relevance_terms(question: str) -> tuple[str, ...]:
    tokens = re.findall(r"[a-z0-9]+", question.casefold())
    return tuple(
        dict.fromkeys(
            token
            for token in tokens
            if len(token) >= 3 and token not in _RELEVANCE_STOPWORDS
        )
    )


def _identity_anchor_terms(question: str) -> tuple[str, ...]:
    match = re.search(
        r"\b(?:what is|who is)\s+(.+?)(?:['’]s)(?:\s|$)",
        question,
        flags=re.IGNORECASE,
    )
    if match is None:
        return ()
    return tuple(re.findall(r"[a-z0-9]+", match.group(1).casefold()))


def _has_query_relevance(question: str, text: str) -> bool:
    """Require bounded lexical evidence that a result addresses the inquiry.

    This is deliberately conservative: it does not prove a claim, but it keeps
    obviously unrelated search noise from becoming promotion-allowed evidence.
    Person/organization questions phrased as ``what is X's`` must contain the
    full identity anchor, not merely adjacent organizational vocabulary.
    """
    terms = _relevance_terms(question)
    if not terms:
        return False
    haystack_tokens = set(re.findall(r"[a-z0-9]+", text.casefold()))
    identity_terms = _identity_anchor_terms(question)
    if identity_terms and not all(term in haystack_tokens for term in identity_terms):
        return False
    matches = sum(term in haystack_tokens for term in terms)
    required = 1 if len(terms) <= 2 else 2
    return matches >= required


def _explicit_http_urls(question: str) -> list[str]:
    """Return validated URLs while removing only recognized presentation syntax.

    A terminal ASCII full stop on an otherwise bare URL is treated as prose
    punctuation. Operators can use angle brackets or a code span when a URL
    intentionally ends in a full stop. Other legal trailing URL characters are
    preserved.
    """
    candidates: list[tuple[int, str]] = []
    covered_spans: list[tuple[int, int]] = []

    def _span_is_covered(position: int) -> bool:
        return any(start <= position < end for start, end in covered_spans)

    def _collect_parenthesized(marker_start: int, url_start: int) -> None:
        depth = 0
        for index in range(url_start, len(question)):
            character = question[index]
            if character.isspace() or character in "<>\"'`":
                break
            if character == "(":
                depth += 1
            elif character == ")":
                if depth == 0:
                    candidates.append((url_start, question[url_start:index]))
                    covered_spans.append((marker_start, index + 1))
                    break
                depth -= 1

    for marker in re.finditer(r"\]\((?=https?://)", question, flags=re.IGNORECASE):
        _collect_parenthesized(marker.start(), marker.end())

    for marker in re.finditer(r"\((?=https?://)", question, flags=re.IGNORECASE):
        if not _span_is_covered(marker.start()):
            _collect_parenthesized(marker.start(), marker.end())

    for match in re.finditer(r"<(https?://[^\s<>\"']+)>", question, flags=re.IGNORECASE):
        candidates.append((match.start(1), match.group(1)))
        covered_spans.append((match.start(), match.end()))

    for match in re.finditer(r"`(https?://[^\s`]+)`", question, flags=re.IGNORECASE):
        candidates.append((match.start(1), match.group(1)))
        covered_spans.append((match.start(), match.end()))

    for match in re.finditer(r"https?://[^\s<>\"'`]+", question, flags=re.IGNORECASE):
        if _span_is_covered(match.start()):
            continue
        candidate = match.group(0)
        if candidate.endswith("."):
            candidate = candidate[:-1]
        candidates.append((match.start(), candidate))

    urls: list[str] = []
    for _, candidate in sorted(candidates):
        normalized = validate_http_url(candidate)
        if normalized not in urls:
            urls.append(normalized)
    return urls


class ResearchRunnerError(RuntimeError):
    """Raised when the research pipeline fails."""


def _default_budget() -> ResearchBudget:
    return ResearchBudget(
        max_calls=8,
        max_tokens=80_000,
        max_cost_usd=8.0,
        max_context_bytes=800_000,
    )


def _request_input_sha256(
    inquiry: Inquiry,
    *,
    adapter: str,
    query: str | None = None,
    url: str | None = None,
) -> str:
    """Hash the exact bounded input authorized for one network operation."""
    packet = {
        "adapter": adapter,
        "inquiry_id": inquiry.id,
        "query": query,
        "url": url,
    }
    encoded = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _merge_unique_results(
    target: list[dict[str, object]], additions: list[dict[str, object]]
) -> None:
    """Merge discovery results by normalized URL, preserving lane order."""
    seen = {
        str(result.get("url", "")).strip().casefold().rstrip("/")
        for result in target
        if str(result.get("url", "")).strip()
    }
    for result in additions:
        normalized = str(result.get("url", "")).strip().casefold().rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            target.append(result)


def _try_authorize_adapter(
    vault: Path,
    adapter_name: str,
    request_input_sha256: str,
    sensitivity: Sensitivity,
) -> bool:
    """Try to authorize one extraction adapter. Returns True if allowed.

    Each denial is durably recorded by require_egress. Returns False for
    denied/undeclared adapters so the escalation ladder can continue.
    """
    provider_info = _ADAPTER_PROVIDERS.get(adapter_name)
    if provider_info is None:
        return False
    provider, model = provider_info
    try:
        require_egress(
            vault,
            EgressRequest(
                provider=provider,
                model=model,
                purpose="research",
                sensitivity=sensitivity,
                request_input_sha256=request_input_sha256,
            ),
        )
        return True
    except EgressDenied:
        return False


def _save_preserved_source(
    vault: Path,
    run_dir: Path,
    url: str,
    content: str,
    *,
    method: str,
) -> dict[str, str]:
    """Save extracted page content. Label this as a derived extraction artifact.

    The saved content is truncated, transformed Markdown — NOT exact fetched
    source bytes. It is labeled accordingly.
    """
    content_bytes = content.encode("utf-8")
    source_hash = hashlib.sha256(content_bytes).hexdigest()
    source_filename = f"{source_hash}.md"
    source_path = run_dir / source_filename

    atomic_write_text(vault, source_path.relative_to(vault), content)

    return {
        "source_hash": source_hash,
        "source_path": source_path.relative_to(vault).as_posix(),
        "url": url,
        "method": method,
        "artifact_kind": "derived_extraction",
    }


def _reserve_adapter_call(
    ledger: ResearchLedger,
    provider: str,
    model: str,
    reserved_context_bytes: int,
) -> dict[str, object]:
    """Reserve budget and a receipt slot before an adapter can run."""
    ledger.record_call(
        lane="collection",
        provider=provider,
        model=model,
        success=False,
        tokens=0,
        cost_usd=0.0,
        context_bytes=reserved_context_bytes,
    )
    return ledger.calls[-1]


def _finish_adapter_call(
    reservation: dict[str, object],
    *,
    success: bool,
    context_bytes: int,
) -> None:
    """Reconcile a preflight reservation with the completed adapter attempt."""
    reserved_context_bytes = reservation.get("context_bytes")
    if not isinstance(reserved_context_bytes, int):
        raise RuntimeError("adapter reservation has invalid context accounting")
    if context_bytes < 0 or context_bytes > reserved_context_bytes:
        raise RuntimeError("adapter context exceeded its preflight reservation")
    reservation["success"] = success
    reservation["context_bytes"] = context_bytes


def _preflight_adapter_call(
    ledger: ResearchLedger,
    provider: str,
    model: str,
    reserved_context_bytes: int,
) -> None:
    """Check capacity before authorization without consuming a receipt slot."""
    reservation = _reserve_adapter_call(
        ledger,
        provider=provider,
        model=model,
        reserved_context_bytes=reserved_context_bytes,
    )
    if not ledger.calls or ledger.calls[-1] is not reservation:
        raise RuntimeError("adapter preflight reservation order was corrupted")
    ledger.calls.pop()


def _write_receipt(vault: Path, run_id: str, ledger: ResearchLedger) -> str:
    """Write the research receipt. Returns the relative path string."""
    receipt_rel = f".constellation/research-runs/{run_id}/receipt.json"
    atomic_write_text(vault, receipt_rel, ledger.receipt_json() + "\n")
    return receipt_rel


def run_inquiry(
    vault: Path | str,
    inquiry: Inquiry,
    *,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    max_pages: int = 5,
) -> dict[str, object]:
    """Execute a bounded web research inquiry.

    Every network adapter is gated through the vault egress policy.
    Returns a research manifest with a durable receipt, preserved sources,
    and extracted pages. Claims are staged separately.
    """
    vault = Path(vault).absolute()
    sensitivity = inquiry.sensitivity
    if max_pages <= 0:
        raise ResearchRunnerError("max_pages must be positive")
    if inquiry.max_unique_sources <= 0:
        raise ResearchRunnerError("max_unique_sources must be positive")

    # Guard: confidential/restricted never leaves the machine
    if sensitivity in {Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED}:
        raise ResearchRunnerError(
            f"web research blocked: sensitivity {sensitivity.value}"
            " requires local-only processing"
        )

    discovery_input_sha256 = _request_input_sha256(
        inquiry,
        adapter="searxng",
        query=inquiry.question,
    )

    # Create the research ledger with budget tracking
    budget = _default_budget()
    ledger = ResearchLedger(
        budget,
        workflow=f"inquiry:{inquiry.id}",
        workflow_version="0.1",
        prompt_version="phase-3",
    )
    run_id = ledger.run_id

    # Create the run directory for extracted artifacts
    run_dir = safe_relative_path(
        vault, Path(".constellation/research-runs") / run_id
    )
    run_dir_path = vault / run_dir
    run_dir_path.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, object]] = []
    preserved_sources: list[dict[str, str]] = []
    sources_discovered = 0
    sources_extracted = 0
    sources_failed = 0
    adapter_attempt_failures = 0
    sources_rejected_irrelevant = 0
    search_results_returned = 0
    queries_used = 0

    def _finish_and_write(force_status: ResearchTerminalState | None = None):
        """Finish the ledger if not already terminal, then write receipt."""
        if ledger.status is None:
            if force_status is not None:
                ledger.finish(force_status, stop_reason=force_status.value)
            elif not results:
                stop_reason = (
                    "no relevant sources could be extracted"
                    if sources_rejected_irrelevant
                    else "no sources could be extracted"
                )
                ledger.finish(
                    ResearchTerminalState.PARTIAL,
                    stop_reason=stop_reason,
                    unresolved_gaps=["inquiry evidence threshold was not met"],
                )
            elif sources_failed > 0 or adapter_attempt_failures > 0:
                ledger.finish(
                    ResearchTerminalState.PARTIAL,
                    stop_reason=(
                        f"{sources_extracted} extracted, {sources_failed} sources failed, "
                        f"{adapter_attempt_failures} adapter attempts failed"
                    ),
                )
            else:
                ledger.finish(
                    ResearchTerminalState.COMPLETED,
                    stop_reason="all selected sources extracted successfully",
                )
        return _write_receipt(vault, run_id, ledger)

    try:
        explicit_urls = _explicit_http_urls(inquiry.question)
        explicit_url_mode = bool(explicit_urls)
        if explicit_url_mode:
            urls_to_extract = explicit_urls[:max_pages]
            sources_discovered = len(urls_to_extract)
        else:
            # ── Step 1: Discover via SearXNG ──
            question_context_bytes = len(inquiry.question.encode("utf-8"))
            _preflight_adapter_call(
                ledger,
                provider="searxng",
                model="searxng-local",
                reserved_context_bytes=question_context_bytes,
            )
            require_egress(
                vault,
                EgressRequest(
                    provider="searxng",
                    model="searxng-local",
                    purpose="research",
                    sensitivity=sensitivity,
                    request_input_sha256=discovery_input_sha256,
                ),
            )

            search_reservation = _reserve_adapter_call(
                ledger,
                provider="searxng",
                model="searxng-local",
                reserved_context_bytes=question_context_bytes,
            )
            search_success = False
            try:
                search_results = search_web(
                    inquiry.question,
                    sensitivity=sensitivity,
                    limit=min(inquiry.max_search_queries * 2, 10),
                )
                search_success = True
            except SearchAdapterError:
                search_results = []
            finally:
                _finish_adapter_call(
                    search_reservation,
                    success=search_success,
                    context_bytes=question_context_bytes,
                )
            queries_used += 1

            def _optional_discovery(adapter: str, search) -> list[dict[str, object]]:
                request_hash = _request_input_sha256(
                    inquiry,
                    adapter=adapter,
                    query=inquiry.question,
                )
                provider, model = _ADAPTER_PROVIDERS[adapter]
                _preflight_adapter_call(
                    ledger,
                    provider=provider,
                    model=model,
                    reserved_context_bytes=question_context_bytes,
                )
                if not _try_authorize_adapter(vault, adapter, request_hash, sensitivity):
                    return []
                reservation = _reserve_adapter_call(
                    ledger,
                    provider=provider,
                    model=model,
                    reserved_context_bytes=question_context_bytes,
                )
                success = False
                try:
                    lane_results = search(
                        inquiry.question,
                        sensitivity=sensitivity,
                        limit=min(inquiry.max_search_queries, 5),
                    )
                    success = True
                    return lane_results
                except SearchAdapterError:
                    return []
                finally:
                    _finish_adapter_call(
                        reservation,
                        success=success,
                        context_bytes=question_context_bytes,
                    )

            _merge_unique_results(search_results, _optional_discovery("exa", exa_search))
            _merge_unique_results(search_results, _optional_discovery("brave", brave_search))
            search_results_returned = len(search_results)

            urls_to_extract = []
            for sr in search_results:
                search_text = "\n".join(
                    str(sr.get(field, "")) for field in ("title", "snippet", "url")
                )
                if not _has_query_relevance(inquiry.question, search_text):
                    sources_rejected_irrelevant += 1
                    continue
                url = str(sr.get("url", ""))
                if not url or url in urls_to_extract:
                    continue
                try:
                    url = validate_http_url(url)
                except UnsafeUrlError:
                    continue
                urls_to_extract.append(url)
                sources_discovered += 1
                if len(urls_to_extract) >= max_pages:
                    break

        # ── Step 2: Extract each URL through the escalation ladder ──
        for url in urls_to_extract:
            if sources_extracted >= inquiry.max_unique_sources:
                break

            page: dict[str, object] | None = None
            attempted_adapter = False

            def _adapter_hash(adapter: str) -> str:
                return _request_input_sha256(inquiry, adapter=adapter, url=url)

            def _try_authorize_extraction_adapter(adapter: str) -> bool:
                if explicit_url_mode and adapter not in _EXPLICIT_URL_ADAPTERS:
                    return False
                provider, model = _ADAPTER_PROVIDERS[adapter]
                _preflight_adapter_call(
                    ledger,
                    provider=provider,
                    model=model,
                    reserved_context_bytes=_MAX_EXTRACTED_CONTEXT_BYTES,
                )
                return _try_authorize_adapter(
                    vault,
                    adapter,
                    _adapter_hash(adapter),
                    sensitivity,
                )

            # Layer 1: Firecrawl
            if page is None and _try_authorize_extraction_adapter("firecrawl"):
                attempted_adapter = True
                adapter_provider, adapter_model = _ADAPTER_PROVIDERS["firecrawl"]
                reservation = _reserve_adapter_call(
                    ledger,
                    provider=adapter_provider,
                    model=adapter_model,
                    reserved_context_bytes=_MAX_EXTRACTED_CONTEXT_BYTES,
                )
                try:
                    from .firecrawl_adapter import (
                        extract_page as firecrawl_extract,
                    )

                    result = firecrawl_extract(
                        url, sensitivity=sensitivity, timeout=30
                    )
                    if result and result.get("markdown"):
                        page = {
                            "url": url,
                            "title": result.get("title", ""),
                            "markdown": str(result.get("markdown", ""))[
                                :_MAX_EXTRACTED_TEXT_CHARS
                            ],
                            "method": "firecrawl",
                            "extracted_at": result.get("extracted_at", ""),
                        }
                except (ImportError, FirecrawlAdapterError):
                    pass
                finally:
                    attempt_success = page is not None
                    _finish_adapter_call(
                        reservation,
                        success=attempt_success,
                        context_bytes=(
                            len(str(page.get("markdown", "")).encode("utf-8"))
                            if page
                            else 0
                        ),
                    )
                    adapter_attempt_failures += int(not attempt_success)

            # Layer 2: Crawl4AI
            if page is None and _try_authorize_extraction_adapter("crawl4ai"):
                attempted_adapter = True
                adapter_provider, adapter_model = _ADAPTER_PROVIDERS["crawl4ai"]
                reservation = _reserve_adapter_call(
                    ledger,
                    provider=adapter_provider,
                    model=adapter_model,
                    reserved_context_bytes=_MAX_EXTRACTED_CONTEXT_BYTES,
                )
                try:
                    from crawl4ai import AsyncWebCrawler
                    import asyncio

                    async def _crawl():
                        async with AsyncWebCrawler() as crawler:
                            return await crawler.arun(url=url)

                    crawl_result = asyncio.run(_crawl())
                    if crawl_result and crawl_result.markdown:
                        page = {
                            "url": url,
                            "title": getattr(crawl_result, "title", "") or "",
                            "markdown": crawl_result.markdown[
                                :_MAX_EXTRACTED_TEXT_CHARS
                            ],
                            "method": "crawl4ai",
                            "extracted_at": datetime.now()
                            .astimezone()
                            .isoformat(),
                        }
                except ImportError:
                    pass
                except Exception:
                    pass
                attempt_success = page is not None
                _finish_adapter_call(
                    reservation,
                    success=attempt_success,
                    context_bytes=(
                        len(str(page.get("markdown", "")).encode("utf-8"))
                        if page
                        else 0
                    ),
                )
                adapter_attempt_failures += int(not attempt_success)

            # Layer 3: Scrapling
            if page is None and _try_authorize_extraction_adapter("scrapling"):
                attempted_adapter = True
                adapter_provider, adapter_model = _ADAPTER_PROVIDERS["scrapling"]
                reservation = _reserve_adapter_call(
                    ledger,
                    provider=adapter_provider,
                    model=adapter_model,
                    reserved_context_bytes=_MAX_EXTRACTED_CONTEXT_BYTES,
                )
                try:
                    import scrapling

                    sp_page = scrapling.fetch(url, timeout=30)
                    if sp_page and sp_page.text:
                        content = str(
                            getattr(sp_page, "markdown", None) or sp_page.text
                        )[:_MAX_EXTRACTED_TEXT_CHARS]
                        if content:
                            page = {
                                "url": url,
                                "title": getattr(sp_page, "title", "") or "",
                                "markdown": content,
                                "method": "scrapling",
                                "extracted_at": datetime.now()
                                .astimezone()
                                .isoformat(),
                            }
                except ImportError:
                    pass
                except Exception:
                    pass
                attempt_success = page is not None
                _finish_adapter_call(
                    reservation,
                    success=attempt_success,
                    context_bytes=(
                        len(str(page.get("markdown", "")).encode("utf-8"))
                        if page
                        else 0
                    ),
                )
                adapter_attempt_failures += int(not attempt_success)

            # Layer 4: Browser Use
            if page is None and _try_authorize_extraction_adapter("browser-use"):
                attempted_adapter = True
                adapter_provider, adapter_model = _ADAPTER_PROVIDERS["browser-use"]
                reservation = _reserve_adapter_call(
                    ledger,
                    provider=adapter_provider,
                    model=adapter_model,
                    reserved_context_bytes=_MAX_EXTRACTED_CONTEXT_BYTES,
                )
                try:
                    from browser_use import Agent
                    import asyncio as _asyncio

                    async def _browse():
                        agent = Agent(
                            task=(
                                f"Extract the main content from {url}"
                                " as clean markdown text."
                                " Return only the extracted content."
                            )
                        )
                        return await agent.run()

                    bu_result = _asyncio.run(_browse())
                    if bu_result and hasattr(bu_result, "final_result"):
                        content = str(bu_result.final_result())[
                            :_MAX_EXTRACTED_TEXT_CHARS
                        ]
                        if content.strip():
                            page = {
                                "url": url,
                                "title": "",
                                "markdown": content,
                                "method": "browser-use",
                                "extracted_at": datetime.now()
                                .astimezone()
                                .isoformat(),
                            }
                except ImportError:
                    pass
                except Exception:
                    pass
                attempt_success = page is not None
                _finish_adapter_call(
                    reservation,
                    success=attempt_success,
                    context_bytes=(
                        len(str(page.get("markdown", "")).encode("utf-8"))
                        if page
                        else 0
                    ),
                )
                adapter_attempt_failures += int(not attempt_success)

            if page:
                markdown_text = str(page.get("markdown", ""))
                page_text = "\n".join(
                    (url, str(page.get("title", "")), markdown_text)
                )
                if not _has_query_relevance(inquiry.question, page_text):
                    sources_rejected_irrelevant += 1
                    continue
                sources_extracted += 1
                preserved = _save_preserved_source(
                    vault,
                    run_dir_path,
                    url,
                    markdown_text,
                    method=str(page.get("method", "unknown")),
                )
                preserved_sources.append(preserved)
                ledger.add_source(
                    source_hash=preserved["source_hash"],
                    url=url,
                )
                results.append(
                    {
                        "url": url,
                        "title": page.get("title", ""),
                        "method": page.get("method", "unknown"),
                        "markdown_preview": markdown_text[:2000],
                        "extracted_at": page.get("extracted_at", ""),
                        "source_hash": preserved["source_hash"],
                        "source_path": preserved["source_path"],
                    }
                )
            elif attempted_adapter:
                sources_failed += 1

        # Normal completion
        receipt_path = _finish_and_write()

    except BudgetExhausted:
        # _exhaust() already made ledger terminal — just write receipt
        receipt_path = _write_receipt(vault, run_id, ledger)
    except Exception:
        if ledger.status is None:
            try:
                ledger.finish(
                    ResearchTerminalState.FAILED,
                    stop_reason="unexpected error during research run",
                )
            except RuntimeError:
                pass  # Already terminal
        receipt_path = _write_receipt(vault, run_id, ledger)
        raise

    return {
        "status": ledger.status.value if ledger.status else "in_progress",
        "run_id": run_id,
        "question": inquiry.question,
        "inquiry_id": inquiry.id,
        "queries_used": queries_used,
        "search_results_returned": search_results_returned,
        "sources_discovered": sources_discovered,
        "sources_extracted": sources_extracted,
        "sources_failed": sources_failed,
        "sources_rejected_irrelevant": sources_rejected_irrelevant,
        "preserved_sources": preserved_sources,
        "results": results,
        "receipt_path": receipt_path,
        "completed_at": datetime.now(UTC).isoformat(),
    }
