"""Tests for the research runner: egress gating, budget enforcement, source preservation,
ledger accounting, failure paths, and receipt generation."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from constellation.egress import EgressDenied
from constellation.models import Inquiry, Sensitivity
from constellation.research_runner import (
    ResearchRunnerError,
    _has_query_relevance,
    _request_input_sha256,
    _try_authorize_adapter,
    run_inquiry,
)
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


def test_confidential_sensitivity_is_blocked_before_any_network_call(
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
        run_inquiry(vault, inquiry, sensitivity=Sensitivity.CONFIDENTIAL)


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
