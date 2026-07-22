"""Tests for the research runner: egress gating, budget enforcement, source preservation,
ledger accounting, failure paths, and receipt generation."""

import json
import sys
import types
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from constellation.egress import EgressDenied
from constellation.models import Inquiry, Sensitivity
from constellation.research_runner import (
    ResearchRunnerError,
    _explicit_http_urls,
    _has_query_relevance,
    _request_input_sha256,
    _try_authorize_adapter,
    run_inquiry,
)
from constellation.url_safety import UnsafeUrlError
from constellation.vault import initialize_vault


def _make_inquiry(**overrides: object) -> Inquiry:
    values: dict[str, object] = {
        "type": "inquiry",
        "title": "public research inquiry",
        "status": "active",
        "sensitivity": Sensitivity.PUBLIC,
        "question": "What does the public source say?",
        "created_at": datetime(2026, 7, 17, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 17, tzinfo=UTC),
    }
    values.update(overrides)
    return Inquiry(**values)


def _declare_provider(
    vault: Path,
    provider: str,
    model: str,
    *,
    enabled: bool = True,
    transport: str = "local",
    max_sensitivity: str = "internal",
    purposes: list[str] | None = None,
    external_enabled: bool = False,
) -> None:
    config_path = vault / ".constellation/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    egress = config.setdefault("egress", {"external_enabled": external_enabled, "providers": {}})
    egress["external_enabled"] = external_enabled
    providers = egress.setdefault("providers", {})
    providers[provider] = {
        "enabled": enabled,
        "transport": transport,
        "max_sensitivity": max_sensitivity,
        "models": [model],
        "purposes": purposes or ["research"],
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")


def _fake_search_results(
    *urls: str,
) -> list[dict[str, object]]:
    return [
        {
            "title": f"Public source result {i}",
            "url": url,
            "snippet": f"The public source says test content {i}",
            "engine": "test",
        }
        for i, url in enumerate(urls, start=1)
    ]


def _fake_firecrawl(url: str, *, sensitivity, timeout=30):
    from datetime import datetime as _dt
    return {
        "url": url,
        "title": "Test Page",
        "markdown": "# Test Content\n\nThis public source contains extracted test content.",
        "extracted_at": _dt.now().astimezone().isoformat(),
    }


# ── egress gating ──────────────────────────────────────────────────


def test_inquiry_discovery_is_denied_before_search_without_declared_egress_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    inquiry = _make_inquiry()

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: pytest.fail(
            "search must not run before egress authorization"
        ),
    )

    with pytest.raises(EgressDenied, match="provider_not_declared"):
        run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)


def test_canonical_confidential_sensitivity_is_blocked_before_any_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")

    inquiry = _make_inquiry(sensitivity=Sensitivity.CONFIDENTIAL)

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: pytest.fail("search must not run for confidential"),
    )

    with pytest.raises(ResearchRunnerError, match="confidential"):
        run_inquiry(vault, inquiry, sensitivity=Sensitivity.INTERNAL)


def test_undeclared_adapter_is_skipped_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")

    assert (
        _try_authorize_adapter(
            vault,
            "browser-use",
            request_input_sha256="b" * 64,
            sensitivity=Sensitivity.INTERNAL,
        )
        is False
    )


def test_adapter_is_authorized_when_declared(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "firecrawl", "firecrawl-local")

    assert (
        _try_authorize_adapter(
            vault,
            "firecrawl",
            request_input_sha256="b" * 64,
            sensitivity=Sensitivity.INTERNAL,
        )
        is True
    )

    ledger = vault / ".constellation/egress-ledger.jsonl"
    events = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    assert len(events) == 1
    assert events[0]["allowed"] is True
    assert events[0]["provider"] == "firecrawl"


def test_denied_adapter_records_denial_and_returns_false(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(
        vault, "firecrawl", "firecrawl-local", purposes=["stage1"]
    )

    assert (
        _try_authorize_adapter(
            vault,
            "firecrawl",
            request_input_sha256="b" * 64,
            sensitivity=Sensitivity.INTERNAL,
        )
        is False
    )

    ledger = vault / ".constellation/egress-ledger.jsonl"
    events = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    assert len(events) == 1
    assert events[0]["allowed"] is False
    assert events[0]["reason"] == "purpose_not_allowed"


def test_request_input_hash_binds_discovery_and_adapter_url():
    inquiry = _make_inquiry()

    hashes = {
        _request_input_sha256(inquiry, adapter="searxng", query=inquiry.question),
        _request_input_sha256(
            inquiry, adapter="firecrawl", url="https://research.example.test/page1"
        ),
        _request_input_sha256(
            inquiry, adapter="firecrawl", url="https://research.example.test/page2"
        ),
        _request_input_sha256(
            inquiry, adapter="raw-http", url="https://research.example.test/page1"
        ),
    }

    assert len(hashes) == 4


def test_discovery_lanes_are_gated_and_merge_unique_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")
    _declare_provider(vault, "exa", "exa-api", transport="external", external_enabled=True)
    _declare_provider(vault, "brave", "brave-api", transport="external", external_enabled=True)
    inquiry = _make_inquiry()

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *_args, **_kwargs: _fake_search_results(
            "https://research.example.test/shared/"
        ),
    )
    monkeypatch.setattr(
        "constellation.research_runner.exa_search",
        lambda *_args, **_kwargs: _fake_search_results(
            "https://research.example.test/shared",
            "https://research.example.test/semantic",
        ),
    )
    monkeypatch.setattr(
        "constellation.research_runner.brave_search",
        lambda *_args, **_kwargs: _fake_search_results(
            "https://RESEARCH.example.test/shared/",
            "https://research.example.test/fresh",
        ),
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["search_results_returned"] == 3
    events = [
        json.loads(line)
        for line in (vault / ".constellation/egress-ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    allowed_discovery = {
        event["provider"]
        for event in events
        if event["allowed"] and event["provider"] in {"searxng", "exa", "brave"}
    }
    assert allowed_discovery == {"searxng", "exa", "brave"}


def test_relevance_matching_uses_whole_tokens_not_name_substrings():
    question = "What is Hajime Eda's current role at Toshiba and battery mandate?"

    assert not _has_query_relevance(
        question,
        "Toshiba corporate battery business executive directory",
    )
    assert _has_query_relevance(
        question,
        "Hajime Eda leads Toshiba battery business development",
    )


def test_explicit_url_skips_discovery_and_uses_gated_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "firecrawl", "firecrawl-local")
    explicit_url = "https://research.example.test/article"
    inquiry = _make_inquiry(
        question=f"{explicit_url}\nExtract this article and summarize its key facts.",
        max_unique_sources=1,
    )

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *_args, **_kwargs: pytest.fail(
            "explicit URL inquiry must not use search discovery"
        ),
    )
    monkeypatch.setattr(
        "constellation.research_runner.exa_search",
        lambda *_args, **_kwargs: pytest.fail(
            "explicit URL inquiry must not use Exa discovery"
        ),
    )
    monkeypatch.setattr(
        "constellation.research_runner.brave_search",
        lambda *_args, **_kwargs: pytest.fail(
            "explicit URL inquiry must not use Brave discovery"
        ),
    )
    monkeypatch.setattr("constellation.firecrawl_adapter.extract_page", _fake_firecrawl)

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "completed"
    assert result["queries_used"] == 0
    assert result["sources_discovered"] == 1
    assert result["sources_extracted"] == 1
    receipt_path = result["receipt_path"]
    assert isinstance(receipt_path, str)
    receipt = json.loads((vault / receipt_path).read_text(encoding="utf-8"))
    assert receipt["sources"][0]["url"] == explicit_url
    events = [
        json.loads(line)
        for line in (vault / ".constellation/egress-ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [event["provider"] for event in events] == ["firecrawl"]
    assert events[0]["request_input_sha256"] == _request_input_sha256(
        inquiry, adapter="firecrawl", url=explicit_url
    )


def test_explicit_url_parser_preserves_balanced_path_parentheses() -> None:
    url = "https://research.example.test/wiki/Function_(mathematics)"

    assert _explicit_http_urls(f"Read {url}") == [url]
    assert _explicit_http_urls(f"Read [source]({url})") == [url]


def test_explicit_url_parser_preserves_legal_trailing_path_characters() -> None:
    semicolon_url = "https://research.example.test/path;"
    exclamation_url = "https://research.example.test/path!"

    assert _explicit_http_urls(f"Read {semicolon_url} {exclamation_url}") == [
        semicolon_url,
        exclamation_url,
    ]


def test_explicit_url_parser_removes_only_presentation_delimiters() -> None:
    url = "https://research.example.test/article"

    assert _explicit_http_urls(f"Read ({url})") == [url]
    assert _explicit_http_urls(f"Read {url}.") == [url]
    assert _explicit_http_urls(f"Read `{url}`") == [url]


@pytest.mark.parametrize(
    ("question", "max_pages"),
    [
        ("Read https://research.example.test/article", -1),
        ("Read https://research.example.test/article", 0),
        ("Research ExampleCo acquisition plans", 0),
    ],
)
def test_nonpositive_page_bound_rejected_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    max_pages: int,
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    inquiry = _make_inquiry(question=question)
    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *_args, **_kwargs: pytest.fail("invalid bound must fail before discovery"),
    )

    with pytest.raises(ResearchRunnerError, match="max_pages"):
        run_inquiry(
            vault,
            inquiry,
            sensitivity=Sensitivity.PUBLIC,
            max_pages=max_pages,
        )

    assert not (vault / ".constellation/egress-ledger.jsonl").exists()


@pytest.mark.parametrize("max_unique_sources", [-1, 0])
def test_nonpositive_unique_source_bound_rejected_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    max_unique_sources: int,
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    inquiry = _make_inquiry(
        question="Read https://research.example.test/article",
        max_unique_sources=max_unique_sources,
    )
    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid unique-source bound must fail before extraction"
        ),
    )

    with pytest.raises(ResearchRunnerError, match="max_unique_sources"):
        run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert not (vault / ".constellation/egress-ledger.jsonl").exists()
    assert list((vault / ".constellation/research-runs").glob("*/receipt.json")) == []


def test_explicit_url_never_uses_raw_http_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "raw-http", "raw-http-local")
    inquiry = _make_inquiry(question="Read https://research.example.test/article")
    calls = 0

    def _raw_network_call(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("raw HTTP fallback must remain disabled")

    monkeypatch.setattr(urllib.request, "urlopen", _raw_network_call)

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert calls == 0
    assert result["status"] == "partial"
    assert result["sources_extracted"] == 0


def test_fallback_attempts_are_all_receipted_and_failure_blocks_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")
    _declare_provider(vault, "firecrawl", "firecrawl-local")
    _declare_provider(vault, "crawl4ai", "crawl4ai-local")
    inquiry = _make_inquiry()
    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *_args, **_kwargs: _fake_search_results(
            "https://research.example.test/article"
        ),
    )

    from constellation.firecrawl_adapter import FirecrawlAdapterError

    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FirecrawlAdapterError("Firecrawl unavailable")
        ),
    )

    class _Crawler:
        async def __aenter__(self) -> "_Crawler":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def arun(self, *, url: str) -> object:
            return types.SimpleNamespace(
                markdown=f"This public source contains evidence from {url}", title=""
            )

    monkeypatch.setitem(
        sys.modules,
        "crawl4ai",
        types.SimpleNamespace(AsyncWebCrawler=_Crawler),
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "partial"
    assert result["sources_extracted"] == 1
    receipt_path = result["receipt_path"]
    assert isinstance(receipt_path, str)
    receipt = json.loads((vault / receipt_path).read_text(encoding="utf-8"))
    assert [(call["provider"], call["success"]) for call in receipt["calls"]] == [
        ("searxng", True),
        ("firecrawl", False),
        ("crawl4ai", True),
    ]
    assert receipt["usage"]["calls"] == 3
    assert receipt["usage"]["failed_calls"] == 1
    assert receipt["promotion_allowed"] is False


def test_call_budget_is_reserved_before_adapter_invocation_and_receipted_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    for provider, model in (
        ("searxng", "searxng-local"),
        ("firecrawl", "firecrawl-local"),
        ("crawl4ai", "crawl4ai-local"),
        ("scrapling", "scrapling-local"),
        ("browser-use", "browser-use-local"),
    ):
        _declare_provider(vault, provider, model)
    inquiry = _make_inquiry(max_unique_sources=2)
    actual_calls: list[str] = []

    def _search(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        actual_calls.append("searxng")
        return _fake_search_results(
            "https://research.example.test/page1",
            "https://research.example.test/page2",
        )

    monkeypatch.setattr("constellation.research_runner.search_web", _search)

    from constellation.firecrawl_adapter import FirecrawlAdapterError

    def _firecrawl(*_args: object, **_kwargs: object) -> object:
        actual_calls.append("firecrawl")
        raise FirecrawlAdapterError("blocked by deterministic fake")

    monkeypatch.setattr("constellation.firecrawl_adapter.extract_page", _firecrawl)

    class _Crawler:
        async def __aenter__(self) -> "_Crawler":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def arun(self, *, url: str) -> object:
            actual_calls.append("crawl4ai")
            raise RuntimeError(f"blocked deterministic Crawl4AI call for {url}")

    def _scrapling(*_args: object, **_kwargs: object) -> object:
        actual_calls.append("scrapling")
        raise RuntimeError("blocked deterministic Scrapling call")

    class _BrowserAgent:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self) -> object:
            actual_calls.append("browser-use")
            raise RuntimeError("blocked deterministic Browser Use call")

    monkeypatch.setitem(
        sys.modules,
        "crawl4ai",
        types.SimpleNamespace(AsyncWebCrawler=_Crawler),
    )
    monkeypatch.setitem(
        sys.modules,
        "scrapling",
        types.SimpleNamespace(fetch=_scrapling),
    )
    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        types.SimpleNamespace(Agent=_BrowserAgent),
    )

    result = run_inquiry(
        vault,
        inquiry,
        sensitivity=Sensitivity.PUBLIC,
        max_pages=2,
    )

    assert result["status"] == "budget_exhausted"
    receipt_path = result["receipt_path"]
    assert isinstance(receipt_path, str)
    receipt = json.loads((vault / receipt_path).read_text(encoding="utf-8"))
    receipted_calls = [call["provider"] for call in receipt["calls"]]
    assert actual_calls == receipted_calls
    assert len(actual_calls) == 6
    assert receipt["usage"]["calls"] == 6
    assert receipt["promotion_allowed"] is False
    egress_events = [
        json.loads(line)
        for line in (vault / ".constellation/egress-ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    authorized_calls = [
        event["provider"] for event in egress_events if event["allowed"]
    ]
    assert actual_calls == authorized_calls


def test_unexpected_firecrawl_error_reconciles_reserved_context_before_reraise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "firecrawl", "firecrawl-local")
    inquiry = _make_inquiry(question="Read https://research.example.test/article")

    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("unexpected deterministic Firecrawl failure")
        ),
    )

    with pytest.raises(RuntimeError, match="unexpected deterministic"):
        run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    receipts = list((vault / ".constellation/research-runs").glob("*/receipt.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert [(call["provider"], call["success"], call["context_bytes"]) for call in receipt["calls"]] == [
        ("firecrawl", False, 0)
    ]
    assert receipt["promotion_allowed"] is False


def test_scrapling_preferred_markdown_is_bounded_before_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    for provider, model in (
        ("searxng", "searxng-local"),
        ("firecrawl", "firecrawl-local"),
        ("crawl4ai", "crawl4ai-local"),
        ("scrapling", "scrapling-local"),
    ):
        _declare_provider(vault, provider, model)
    inquiry = _make_inquiry()
    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *_args, **_kwargs: _fake_search_results(
            "https://research.example.test/article"
        ),
    )

    from constellation.firecrawl_adapter import FirecrawlAdapterError

    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FirecrawlAdapterError("deterministic Firecrawl failure")
        ),
    )

    class _Crawler:
        async def __aenter__(self) -> "_Crawler":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def arun(self, *, url: str) -> object:
            raise RuntimeError(f"deterministic Crawl4AI failure for {url}")

    monkeypatch.setitem(
        sys.modules,
        "crawl4ai",
        types.SimpleNamespace(AsyncWebCrawler=_Crawler),
    )
    oversized_markdown = "This public source says " + ("🔥" * 50_000)
    monkeypatch.setitem(
        sys.modules,
        "scrapling",
        types.SimpleNamespace(
            fetch=lambda *_args, **_kwargs: types.SimpleNamespace(
                text="fallback text",
                markdown=oversized_markdown,
                title="Test Page",
            )
        ),
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "partial"
    assert result["sources_extracted"] == 1
    receipt_path = result["receipt_path"]
    assert isinstance(receipt_path, str)
    receipt = json.loads((vault / receipt_path).read_text(encoding="utf-8"))
    scrapling_call = next(
        call for call in receipt["calls"] if call["provider"] == "scrapling"
    )
    assert scrapling_call["success"] is True
    assert 0 < scrapling_call["context_bytes"] <= 200_000
    preserved_sources = result["preserved_sources"]
    assert isinstance(preserved_sources, list)
    assert isinstance(preserved_sources[0], dict)
    source_path = preserved_sources[0]["source_path"]
    assert isinstance(source_path, str)
    assert len((vault / source_path).read_text(encoding="utf-8")) == 50_000


def test_browser_use_empty_result_is_receipted_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")
    _declare_provider(vault, "browser-use", "browser-use-local")
    inquiry = _make_inquiry()
    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *_args, **_kwargs: _fake_search_results(
            "https://research.example.test/article"
        ),
    )

    class _BrowserResult:
        def final_result(self) -> str:
            return ""

    class _BrowserAgent:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self) -> _BrowserResult:
            return _BrowserResult()

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        types.SimpleNamespace(Agent=_BrowserAgent),
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "partial"
    assert result["sources_extracted"] == 0
    receipt_path = result["receipt_path"]
    assert isinstance(receipt_path, str)
    receipt = json.loads((vault / receipt_path).read_text(encoding="utf-8"))
    browser_call = next(
        call for call in receipt["calls"] if call["provider"] == "browser-use"
    )
    assert browser_call["success"] is False
    assert browser_call["context_bytes"] == 0
    assert receipt["usage"]["failed_calls"] == 1
    assert receipt["promotion_allowed"] is False


def test_explicit_url_blocked_redirect_does_not_use_unverified_fallbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    for provider, model in (
        ("firecrawl", "firecrawl-local"),
        ("crawl4ai", "crawl4ai-local"),
        ("scrapling", "scrapling-local"),
        ("browser-use", "browser-use-local"),
    ):
        _declare_provider(vault, provider, model)
    inquiry = _make_inquiry(question="Read https://research.example.test/redirect")

    from constellation.firecrawl_adapter import FirecrawlAdapterError

    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FirecrawlAdapterError("redirect target resolves to a private address")
        ),
    )

    class _ForbiddenCrawler:
        async def __aenter__(self) -> "_ForbiddenCrawler":
            raise AssertionError("explicit URL must not fall back to Crawl4AI")

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setitem(
        sys.modules,
        "crawl4ai",
        types.SimpleNamespace(AsyncWebCrawler=_ForbiddenCrawler),
    )
    monkeypatch.setitem(
        sys.modules,
        "scrapling",
        types.SimpleNamespace(
            fetch=lambda *_args, **_kwargs: pytest.fail(
                "explicit URL must not fall back to Scrapling"
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        types.SimpleNamespace(
            Agent=lambda *_args, **_kwargs: pytest.fail(
                "explicit URL must not fall back to Browser Use"
            )
        ),
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "partial"
    assert result["sources_extracted"] == 0
    receipt_path = result["receipt_path"]
    assert isinstance(receipt_path, str)
    receipt = json.loads((vault / receipt_path).read_text(encoding="utf-8"))
    assert [(call["provider"], call["success"]) for call in receipt["calls"]] == [
        ("firecrawl", False)
    ]
    assert receipt["promotion_allowed"] is False
    events = [
        json.loads(line)
        for line in (vault / ".constellation/egress-ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [event["provider"] for event in events] == ["firecrawl"]


def test_unsafe_explicit_url_fails_before_network_and_writes_failed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    unsafe_host = "".join(chr(value) for value in (49, 50, 55, 46, 48, 46, 48, 46, 49))
    inquiry = _make_inquiry(question=f"Extract http://{unsafe_host}/private")
    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *_args, **_kwargs: pytest.fail("unsafe URL must fail before discovery"),
    )

    with pytest.raises(UnsafeUrlError):
        run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert not (vault / ".constellation/egress-ledger.jsonl").exists()
    receipts = list((vault / ".constellation/research-runs").glob("*/receipt.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["promotion_allowed"] is False


def test_run_inquiry_records_distinct_url_bound_adapter_authorizations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")
    _declare_provider(vault, "firecrawl", "firecrawl-local")
    inquiry = _make_inquiry(max_unique_sources=2)
    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: _fake_search_results(
            "https://research.example.test/page1",
            "https://research.example.test/page2",
        ),
    )
    monkeypatch.setattr("constellation.firecrawl_adapter.extract_page", _fake_firecrawl)

    run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC, max_pages=2)

    events = [
        json.loads(line)
        for line in (vault / ".constellation/egress-ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    firecrawl_hashes = {
        event["request_input_sha256"]
        for event in events
        if event["provider"] == "firecrawl" and event["allowed"]
    }
    assert len(firecrawl_hashes) == 2


# ── receipt and source preservation ─────────────────────────────────


def test_run_inquiry_generates_receipt_and_preserves_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")
    _declare_provider(vault, "firecrawl", "firecrawl-local")

    inquiry = _make_inquiry()

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: _fake_search_results(
            "https://research.example.test/page1"
        ),
    )
    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        _fake_firecrawl,
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "completed"
    assert result["sources_discovered"] == 1
    assert result["sources_extracted"] == 1
    assert result["sources_failed"] == 0
    assert len(result["preserved_sources"]) == 1
    assert "run_id" in result
    assert "receipt_path" in result

    receipt_path = vault / result["receipt_path"]
    assert receipt_path.is_file()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "completed"
    assert receipt["sources"][0]["url"] == "https://research.example.test/page1"
    assert receipt["promotion_allowed"] is True

    preserved = result["preserved_sources"][0]
    source_path = vault / preserved["source_path"]
    assert source_path.is_file()
    content = source_path.read_text(encoding="utf-8")
    assert "Test Content" in content
    assert preserved["artifact_kind"] == "derived_extraction"


def test_no_results_generates_partial_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")

    inquiry = _make_inquiry()

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: [],
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "partial"
    assert result["sources_discovered"] == 0
    assert result["sources_failed"] == 0

    receipt_path = vault / result["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "partial"
    assert "no sources could be extracted" in receipt["stop_reason"]
    assert receipt["promotion_allowed"] is False


def test_irrelevant_search_noise_cannot_produce_completed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")
    _declare_provider(vault, "firecrawl", "firecrawl-local")
    inquiry = _make_inquiry(
        question="What is Hajime Eda's current public role at Toshiba and battery mandate?"
    )
    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: [
            {
                "title": "Math Calculator",
                "url": "https://research.example.test/calculator",
                "snippet": "Evaluate arithmetic expressions online",
                "engine": "test",
            }
        ],
    )
    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        lambda *args, **kwargs: pytest.fail(
            "irrelevant search noise must be rejected before extraction"
        ),
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "partial"
    assert result["search_results_returned"] == 1
    assert result["sources_discovered"] == 0
    assert result["sources_rejected_irrelevant"] == 1
    assert result["sources_extracted"] == 0
    assert result["preserved_sources"] == []
    receipt_path = result["receipt_path"]
    assert isinstance(receipt_path, str)
    receipt = json.loads((vault / receipt_path).read_text(encoding="utf-8"))
    assert receipt["promotion_allowed"] is False
    assert "relevant" in receipt["stop_reason"]


def test_only_declared_adapters_are_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")

    inquiry = _make_inquiry()

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: _fake_search_results(
            "https://research.example.test/page1"
        ),
    )
    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        lambda *a, **kw: pytest.fail(
            "firecrawl should not be called without authorization"
        ),
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["sources_discovered"] == 1
    assert result["sources_extracted"] == 0
    assert len(result["preserved_sources"]) == 0
    assert result["status"] == "partial"


def test_egress_ledger_records_every_authorization_and_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")
    _declare_provider(vault, "firecrawl", "firecrawl-local", enabled=False)

    inquiry = _make_inquiry()

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: _fake_search_results(
            "https://research.example.test/page1"
        ),
    )

    run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    ledger = vault / ".constellation/egress-ledger.jsonl"
    events = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
    ]

    providers_seen = {e["provider"] for e in events}
    assert "searxng" in providers_seen
    for event in events:
        if event["provider"] != "searxng":
            assert event["allowed"] is False


# ── ledger accounting (defect #3 fixes) ─────────────────────────────


def test_search_error_produces_failed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search failure records a failed call and writes a partial receipt."""
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")

    inquiry = _make_inquiry()

    from constellation.search_adapter import SearchAdapterError

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            SearchAdapterError("SearXNG unreachable")
        ),
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "partial"
    assert result["sources_discovered"] == 0

    receipt_path = vault / result["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "partial"
    assert receipt["usage"]["failed_calls"] >= 1
    assert receipt["promotion_allowed"] is False


def test_partial_extraction_with_one_success_one_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One page succeeds, one fails — receipt is partial, promotion forbidden."""
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _declare_provider(vault, "searxng", "searxng-local")
    _declare_provider(vault, "firecrawl", "firecrawl-local")

    inquiry = _make_inquiry(max_unique_sources=5)

    monkeypatch.setattr(
        "constellation.research_runner.search_web",
        lambda *args, **kwargs: _fake_search_results(
            "https://research.example.test/good",
            "https://research.example.test/bad",
        ),
    )

    call_count = [0]

    def _flaky_firecrawl(url: str, *, sensitivity, timeout=30):
        call_count[0] += 1
        if "bad" in url:
            from constellation.firecrawl_adapter import FirecrawlAdapterError
            raise FirecrawlAdapterError("extraction failed")
        return _fake_firecrawl(url, sensitivity=sensitivity, timeout=timeout)

    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        _flaky_firecrawl,
    )

    result = run_inquiry(vault, inquiry, sensitivity=Sensitivity.PUBLIC)

    assert result["status"] == "partial"
    assert result["sources_extracted"] == 1
    assert result["sources_failed"] == 1
    assert len(result["preserved_sources"]) == 1

    receipt_path = vault / result["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "partial"
    assert receipt["promotion_allowed"] is False
    assert receipt["usage"]["failed_calls"] >= 1
