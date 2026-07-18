"""Tests for SearXNG, EXA, and Brave search adapters."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

import pytest

from constellation import search_adapter
from constellation.models import Sensitivity
from constellation.search_adapter import SearchAdapterError


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _headers(request: urllib.request.Request) -> dict[str, str]:
    return {name.casefold(): value for name, value in request.header_items()}


def test_optional_api_lanes_skip_network_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("network must not run without API keys"),
    )

    assert search_adapter.exa_search("public topic", sensitivity=Sensitivity.PUBLIC) == []
    assert search_adapter.brave_search("public topic", sensitivity=Sensitivity.PUBLIC) == []


def test_exa_search_uses_current_env_key_timeout_and_normalized_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "fictional-exa-key")
    captured: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> _Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(
            {
                "results": [
                    {
                        "title": "Semantic result",
                        "url": "https://research.example.test/semantic",
                        "highlights": ["Meaning-matched evidence."],
                        "text": "Fallback text",
                    }
                ]
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    results = search_adapter.exa_search(
        "public topic",
        sensitivity=Sensitivity.PUBLIC,
        limit=4,
        timeout=7,
    )

    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == "https://api.exa.ai/search"
    assert request.get_method() == "POST"
    assert _headers(request)["x-api-key"] == "fictional-exa-key"
    assert isinstance(request.data, bytes)
    assert json.loads(request.data) == {
        "query": "public topic",
        "type": "auto",
        "numResults": 4,
        "contents": {"highlights": True, "text": True},
    }
    assert captured["timeout"] == 7
    assert results == [
        {
            "title": "Semantic result",
            "url": "https://research.example.test/semantic",
            "snippet": "Meaning-matched evidence.",
            "engine": "exa",
        }
    ]


def test_brave_search_applies_freshness_and_normalizes_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "fictional-brave-key")
    captured: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> _Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(
            {
                "web": {
                    "results": [
                        {
                            "title": "Fresh result",
                            "url": "https://research.example.test/fresh",
                            "description": "Past-week evidence.",
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    results = search_adapter.brave_search(
        "public topic",
        sensitivity=Sensitivity.PUBLIC,
        freshness="pw",
        limit=3,
        timeout=9,
    )

    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    parsed = urllib.parse.urlsplit(request.full_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.search.brave.com"
    assert urllib.parse.parse_qs(parsed.query) == {
        "q": ["public topic"],
        "count": ["3"],
        "freshness": ["pw"],
    }
    assert _headers(request)["x-subscription-token"] == "fictional-brave-key"
    assert captured["timeout"] == 9
    assert results == [
        {
            "title": "Fresh result",
            "url": "https://research.example.test/fresh",
            "snippet": "Past-week evidence.",
            "engine": "brave_api",
        }
    ]


def test_brave_time_sensitive_uses_past_week(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_brave(query: str, **kwargs: object) -> list[dict[str, object]]:
        captured.update({"query": query, **kwargs})
        return []

    monkeypatch.setattr(search_adapter, "brave_search", fake_brave)

    assert search_adapter.brave_search_time_sensitive(
        "public topic",
        sensitivity=Sensitivity.PUBLIC,
        limit=2,
        timeout=6,
    ) == []
    assert captured == {
        "query": "public topic",
        "sensitivity": Sensitivity.PUBLIC,
        "freshness": "pw",
        "limit": 2,
        "timeout": 6,
    }


def test_optional_api_lanes_reject_restricted_search_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    with pytest.raises(SearchAdapterError, match="restricted"):
        search_adapter.exa_search("private topic", sensitivity=Sensitivity.RESTRICTED)
    with pytest.raises(SearchAdapterError, match="confidential"):
        search_adapter.brave_search("private topic", sensitivity=Sensitivity.CONFIDENTIAL)
