import json

import pytest

from constellation.models import ResearchTerminalState
from constellation.research import BudgetExhausted, ResearchBudget, ResearchLedger


def budget():
    return ResearchBudget(max_calls=8, max_tokens=800, max_cost_usd=8.0, max_context_bytes=8000)


def test_collection_cannot_spend_locked_final_quarter():
    ledger = ResearchLedger(budget())
    for _ in range(6):
        ledger.record_call(
            lane="collection", provider="fictional", model="fictional-model-v1", success=True,
            tokens=100, cost_usd=1.0, context_bytes=1000,
        )
    with pytest.raises(BudgetExhausted):
        ledger.record_call(
            lane="collection", provider="fictional", model="fictional-model-v1", success=True,
            tokens=1, cost_usd=0.01, context_bytes=1,
        )
    assert ledger.status is ResearchTerminalState.BUDGET_EXHAUSTED
    assert ledger.promotion_allowed is False


def test_synthesis_can_use_reserved_capacity_and_failed_calls_count():
    ledger = ResearchLedger(budget())
    ledger.record_call(
        lane="collection", provider="fictional", model="fictional-model-v1", success=False,
        tokens=600, cost_usd=6.0, context_bytes=6000,
    )
    ledger.record_call(
        lane="synthesis", provider="fictional", model="fictional-model-v1", success=True,
        tokens=200, cost_usd=2.0, context_bytes=2000,
    )
    receipt = ledger.receipt()
    assert receipt["usage"]["calls"] == 2
    assert receipt["usage"]["tokens"] == 800
    assert receipt["lanes"]["collection"]["failed_calls"] == 1


def test_unknown_provider_usage_remains_unknown_not_zero():
    ledger = ResearchLedger(budget())
    ledger.record_call(
        lane="collection", provider="fictional", model="fictional-model-v1", success=True,
        tokens=None, cost_usd=None, context_bytes=100,
        estimated_tokens=50, estimated_cost_usd=0.5,
    )
    receipt = ledger.receipt()
    assert receipt["calls"][0]["tokens"] == "unknown"
    assert receipt["calls"][0]["cost_usd"] == "unknown"
    assert receipt["usage"]["tokens"] == "unknown"
    assert receipt["usage"]["cost_usd"] == "unknown"


def test_source_ledger_deduplicates_hashes_and_urls():
    ledger = ResearchLedger(budget())
    assert ledger.add_source(source_hash="a" * 64, url="https://research.example.test/one") is True
    assert ledger.add_source(source_hash="a" * 64, url="https://research.example.test/two") is False
    assert ledger.add_source(source_hash="b" * 64, url="https://research.example.test/one") is False
    assert len(ledger.receipt()["sources"]) == 1


def test_terminal_states_and_json_receipt_block_partial_promotion():
    ledger = ResearchLedger(budget())
    ledger.finish(
        ResearchTerminalState.PARTIAL,
        stop_reason="insufficient evidence",
        pivotal_claims=["Fictional claim"],
        contradictions=["Fictional contradiction"],
        unresolved_gaps=["Fictional gap"],
    )
    assert ledger.promotion_allowed is False
    receipt = json.loads(ledger.receipt_json())
    assert receipt["status"] == "partial"
    assert receipt["stop_reason"] == "insufficient evidence"
    assert receipt["pivotal_claims"] == ["Fictional claim"]
    with pytest.raises(RuntimeError):
        ledger.record_call(
            lane="evaluation", provider="fictional", model="fictional-model-v1", success=True,
            tokens=1, cost_usd=0.0, context_bytes=1,
        )


def test_completed_run_can_promote():
    ledger = ResearchLedger(budget())
    ledger.finish(ResearchTerminalState.COMPLETED, stop_reason="completed")
    assert ledger.promotion_allowed is True


def test_receipt_records_exact_model_usage_retries_and_evidence_hashes():
    ledger = ResearchLedger(
        budget(),
        workflow="fictional-triangulation",
        workflow_version="0.4.1",
        prompt_version="stage1-v2",
    )
    ledger.add_source(
        source_hash="a" * 64,
        url="https://research.example.test/source",
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        anchor="P0002:L0012",
        sensitivity="internal",
        origin_cluster="fictional-primary",
    )
    ledger.record_call(
        lane="synthesis",
        provider="fictional-provider",
        model="fictional-model-v2",
        model_version="2026-07-01",
        provider_request_id="req-fictional-1",
        success=True,
        tokens=175,
        input_tokens=100,
        output_tokens=50,
        reasoning_tokens=20,
        cache_read_tokens=5,
        cache_write_tokens=0,
        cost_usd=0.25,
        context_bytes=400,
        estimated_tokens=180,
        estimated_cost_usd=0.3,
        prompt_sha256="b" * 64,
        evidence_packet_sha256="c" * 64,
        retry_attempt=1,
        duration_ms=321,
    )
    ledger.finish(ResearchTerminalState.COMPLETED, stop_reason="quality gates passed")

    receipt = ledger.receipt()

    assert receipt["version"] == 2
    assert receipt["workflow"] == {
        "name": "fictional-triangulation",
        "version": "0.4.1",
        "prompt_version": "stage1-v2",
    }
    assert receipt["usage"]["tokens"] == 175
    assert receipt["usage"]["input_tokens"] == 100
    assert receipt["usage"]["output_tokens"] == 50
    assert receipt["usage"]["reasoning_tokens"] == 20
    assert receipt["usage"]["cache_read_tokens"] == 5
    assert receipt["usage"]["retry_calls"] == 1
    call = receipt["calls"][0]
    assert call["provider"] == "fictional-provider"
    assert call["model"] == "fictional-model-v2"
    assert call["model_version"] == "2026-07-01"
    assert call["usage_source"] == "provider"
    assert call["prompt_sha256"] == "b" * 64
    assert call["evidence_packet_sha256"] == "c" * 64
    assert receipt["sources"][0]["anchor"] == "P0002:L0012"
    assert receipt["evidence_set_sha256"]
    assert receipt["started_at"]
    assert receipt["finished_at"]
