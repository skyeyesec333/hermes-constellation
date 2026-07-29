"""Tests for egress-gated external intelligence feeders."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from constellation.feeders import (
    FeederError,
    FeederRequest,
    collect_from_feeder,
)
from constellation.frontmatter import render_frontmatter
from constellation.models import EntityKind, EntityRecord, Sensitivity, generate_ulid
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
PROVIDER = "test-feeder-provider"
MODEL = "test-feeder-model"


# ── Synthetic API responses ────────────────────────────────────────────

def _gdelt_response() -> bytes:
    return json.dumps({
        "articles": [
            {"title": "TestCo announces expansion", "url": "https://example.test/1", "tone": {}},
        ]
    }).encode()


def _edgar_response() -> bytes:
    return json.dumps({
        "hits": {
            "hits": [
                {"_source": {
                    "companyName": "TestCo",
                    "formType": "10-K",
                    "filedAt": "2026-01-15",
                    "fileDescription": "Annual Report",
                    "accessionNumber": "0001234567-26-000001",
                }}
            ]
        }
    }).encode()


def _polymarket_response() -> bytes:
    return json.dumps([
        {"title": "Will TestCo exceed revenue?", "question": "Q4 2026", "outcomePrices": [0.65, 0.35], "volume": 500000, "closed": False},
    ]).encode()


# ── Fixtures ────────────────────────────────────────────────────────────

def _declare_egress_provider(vault: Path, *, max_sensitivity: str = "restricted") -> None:
    path = vault / ".constellation/config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["egress"] = {
        "external_enabled": True,
        "providers": {
            PROVIDER: {
                "enabled": True,
                "transport": "external",
                "max_sensitivity": max_sensitivity,
                "models": [MODEL],
                "purposes": ["research"],
            }
        },
    }
    path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")


def _setup_vault(tmp_path: Path, *, authorized: bool = True) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    if authorized:
        _declare_egress_provider(vault)

    subject = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.ORGANIZATION,
        title="TestCo",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "entities" / f"{subject.id}.md").write_text(
        render_frontmatter(subject.model_dump(mode="json", exclude_none=True), "# TestCo\n"),
        encoding="utf-8",
    )
    return vault, subject.id


def _make_request(vault: Path, subject_id: str, source: str = "gdelt") -> FeederRequest:
    return FeederRequest(
        source=source,
        query="TestCo",
        subject_id=subject_id,
        provider=PROVIDER,
        model=MODEL,
    )


def _single_receipt(vault: Path) -> dict:
    paths = list((vault / ".constellation/feeder-receipts").glob("*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


# ── Circuit breaker ───────────────────────────────────────────────────

from constellation.feeders import FeederResult  # noqa: E402


def _fake_collector(status: str, calls: list[str]):
    def _collect(vault, request, *, subject):
        calls.append(request.source)
        return FeederResult(status=status, receipt_path="")
    return _collect


def test_circuit_opens_after_consecutive_failures(tmp_path: Path, monkeypatch) -> None:
    import constellation.feeders as feeders

    vault, subject_id = _setup_vault(tmp_path)
    calls: list[str] = []
    monkeypatch.setitem(feeders._COLLECTORS, "gdelt", _fake_collector("failed", calls))

    for _ in range(3):
        result = collect_from_feeder(vault, _make_request(vault, subject_id))
        assert result.status == "failed"

    result = collect_from_feeder(vault, _make_request(vault, subject_id))
    assert result.status == "circuit_open"
    assert len(calls) == 3  # 4th attempt never reached the collector
    assert result.error and "circuit" in result.error.lower()


def test_circuit_open_writes_truthful_receipt(tmp_path: Path, monkeypatch) -> None:
    import constellation.feeders as feeders

    vault, subject_id = _setup_vault(tmp_path)
    monkeypatch.setitem(feeders._COLLECTORS, "gdelt", _fake_collector("failed", []))

    for _ in range(3):
        collect_from_feeder(vault, _make_request(vault, subject_id))
    collect_from_feeder(vault, _make_request(vault, subject_id))

    receipts = sorted((vault / ".constellation/feeder-receipts").glob("*.json"))
    last = json.loads(receipts[-1].read_text(encoding="utf-8"))
    assert last["status"] == "circuit_open"
    assert last["source"] == "gdelt"


def test_success_resets_circuit(tmp_path: Path, monkeypatch) -> None:
    import constellation.feeders as feeders

    vault, subject_id = _setup_vault(tmp_path)
    calls: list[str] = []
    monkeypatch.setitem(feeders._COLLECTORS, "gdelt", _fake_collector("failed", calls))
    for _ in range(2):
        collect_from_feeder(vault, _make_request(vault, subject_id))

    monkeypatch.setitem(feeders._COLLECTORS, "gdelt", _fake_collector("ok", calls))
    collect_from_feeder(vault, _make_request(vault, subject_id))

    monkeypatch.setitem(feeders._COLLECTORS, "gdelt", _fake_collector("failed", calls))
    for _ in range(2):
        result = collect_from_feeder(vault, _make_request(vault, subject_id))
    assert result.status == "failed"  # counter restarted after success


def test_denied_does_not_count_toward_circuit(tmp_path: Path, monkeypatch) -> None:
    import constellation.feeders as feeders

    vault, subject_id = _setup_vault(tmp_path)
    calls: list[str] = []
    monkeypatch.setitem(feeders._COLLECTORS, "gdelt", _fake_collector("denied", calls))
    for _ in range(4):
        result = collect_from_feeder(vault, _make_request(vault, subject_id))
        assert result.status == "denied"
    assert len(calls) == 4  # never short-circuited


def test_manual_reset_reopens_lane(tmp_path: Path, monkeypatch) -> None:
    import constellation.feeders as feeders

    vault, subject_id = _setup_vault(tmp_path)
    calls: list[str] = []
    monkeypatch.setitem(feeders._COLLECTORS, "gdelt", _fake_collector("failed", calls))
    for _ in range(3):
        collect_from_feeder(vault, _make_request(vault, subject_id))

    feeders.reset_feeder_circuit(vault, "gdelt")
    result = collect_from_feeder(vault, _make_request(vault, subject_id))
    assert result.status == "failed"  # lane callable again
    assert len(calls) == 4


# ── Denied egress ───────────────────────────────────────────────────────

def test_denied_egress_produces_denial_result_no_network_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, subject_id = _setup_vault(tmp_path, authorized=False)

    # Prove no network call happens
    def _fail_urlopen(*_a, **_kw):
        pytest.fail("network call must not happen before egress authorization")

    monkeypatch.setattr("constellation.feeders.urllib.request.urlopen", _fail_urlopen)

    result = collect_from_feeder(vault, _make_request(vault, subject_id))
    assert result.status == "denied"
    assert not result.source_ids
    receipt = _single_receipt(vault)
    assert receipt["status"] == "denied"


# ── Missing / invalid subject ──────────────────────────────────────────

def test_missing_subject_fails_before_egress(tmp_path: Path) -> None:
    vault, _ = _setup_vault(tmp_path)
    fake_id = generate_ulid()
    with pytest.raises(FeederError, match="canonical subject"):
        collect_from_feeder(vault, _make_request(vault, fake_id))

    # No egress ledger entry should exist
    assert not (vault / ".constellation/egress-ledger.jsonl").exists()


# ── Successful GDELT collection ─────────────────────────────────────────

def test_gdelt_success_preserves_source_and_writes_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, subject_id = _setup_vault(tmp_path)

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, size=-1):
            return _gdelt_response()

    monkeypatch.setattr("constellation.feeders.urllib.request.urlopen", lambda url, timeout: _FakeResponse())

    result = collect_from_feeder(vault, _make_request(vault, subject_id))
    assert result.status == "ok"
    assert len(result.source_ids) == 1
    assert result.items_found == 1

    receipt = _single_receipt(vault)
    assert receipt["status"] == "ok"
    assert receipt["response_sha256"]
    assert receipt["items_found"] == 1

    # Egress ledger should have an authorized entry
    lines = (vault / ".constellation/egress-ledger.jsonl").read_text().splitlines()
    assert len(lines) >= 1
    event = json.loads(lines[0])
    assert event["allowed"] is True


# ── EDGAR success ───────────────────────────────────────────────────────

def test_edgar_success_preserves_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, subject_id = _setup_vault(tmp_path)

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, size=-1):
            return _edgar_response()

    monkeypatch.setattr("constellation.feeders.urllib.request.urlopen", lambda *a, **kw: _FakeResponse())

    result = collect_from_feeder(vault, _make_request(vault, subject_id, source="edgar"))
    assert result.status == "ok"
    assert len(result.source_ids) == 1
    assert result.items_found == 1


# ── Polymarket success ──────────────────────────────────────────────────

def test_polymarket_success_preserves_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, subject_id = _setup_vault(tmp_path)

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, size=-1):
            return _polymarket_response()

    monkeypatch.setattr("constellation.feeders.urllib.request.urlopen", lambda url, timeout: _FakeResponse())

    result = collect_from_feeder(vault, _make_request(vault, subject_id, source="polymarket"))
    assert result.status == "ok"
    assert len(result.source_ids) == 1
    assert result.items_found == 1


# ── Empty response ──────────────────────────────────────────────────────

def test_empty_response_returns_empty_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, subject_id = _setup_vault(tmp_path)

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, size=-1):
            return b""

    monkeypatch.setattr("constellation.feeders.urllib.request.urlopen", lambda url, timeout: _FakeResponse())

    result = collect_from_feeder(vault, _make_request(vault, subject_id))
    assert result.status == "empty"
    assert result.items_found == 0
    assert _single_receipt(vault)["status"] == "empty"


# ── Invalid JSON ────────────────────────────────────────────────────────

def test_invalid_json_writes_failed_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, subject_id = _setup_vault(tmp_path)

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, size=-1):
            return b"not json at all {{{"

    monkeypatch.setattr("constellation.feeders.urllib.request.urlopen", lambda url, timeout: _FakeResponse())

    with pytest.raises(FeederError, match="invalid JSON"):
        collect_from_feeder(vault, _make_request(vault, subject_id))

    assert _single_receipt(vault)["status"] == "failed"


# ── Oversized response ──────────────────────────────────────────────────

def test_oversized_response_fails_with_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, subject_id = _setup_vault(tmp_path)

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, size=-1):
            return b"x" * (10 * 1024 * 1024 + 1)  # exceeds _MAX_RESPONSE_BYTES

    monkeypatch.setattr("constellation.feeders.urllib.request.urlopen", lambda url, timeout: _FakeResponse())

    with pytest.raises(FeederError, match="exceeds"):
        collect_from_feeder(vault, _make_request(vault, subject_id))

    assert _single_receipt(vault)["status"] == "failed"


# ── Transport error ─────────────────────────────────────────────────────

def test_transport_error_writes_failed_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, subject_id = _setup_vault(tmp_path)

    def _fail(*_a, **_kw):
        import urllib.error
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("constellation.feeders.urllib.request.urlopen", _fail)

    with pytest.raises(FeederError, match="unreachable"):
        collect_from_feeder(vault, _make_request(vault, subject_id))

    assert _single_receipt(vault)["status"] == "failed"


# ── FeederRequest validation ────────────────────────────────────────────

def test_feeder_request_rejects_empty_subject_id() -> None:
    with pytest.raises(FeederError, match="subject_id"):
        FeederRequest(source="gdelt", query="test", subject_id="", provider=PROVIDER, model=MODEL)


def test_feeder_request_rejects_unknown_source() -> None:
    with pytest.raises(FeederError, match="unsupported source"):
        FeederRequest(source="unknown", query="test", subject_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", provider=PROVIDER, model=MODEL)


def test_feeder_request_rejects_empty_provider() -> None:
    with pytest.raises(FeederError, match="provider"):
        FeederRequest(source="gdelt", query="test", subject_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", provider="", model=MODEL)


# ── CLI integration ─────────────────────────────────────────────────────

def test_enrich_collect_cli_args() -> None:
    from constellation.cli import build_parser

    values = vars(
        build_parser().parse_args([
            "enrich", "collect", "/tmp/vault", "gdelt", "TestCo",
            "--subject-id", "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "--provider", PROVIDER,
            "--model", MODEL,
        ])
    )
    assert values["enrich_action"] == "collect"
    assert values["source"] == "gdelt"
    assert values["query"] == "TestCo"
    assert values["subject_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def test_enrich_extract_cli_args() -> None:
    from constellation.cli import build_parser

    values = vars(
        build_parser().parse_args([
            "enrich", "extract", "/tmp/vault", "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "--subject-id", "01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "--provider", PROVIDER,
            "--model", MODEL,
        ])
    )
    assert values["enrich_action"] == "extract"
    assert values["source_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
