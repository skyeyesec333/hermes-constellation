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
            lane="collection", provider="fictional", success=True,
            tokens=100, cost_usd=1.0, context_bytes=1000,
        )
    with pytest.raises(BudgetExhausted):
        ledger.record_call(
            lane="collection", provider="fictional", success=True,
            tokens=1, cost_usd=0.01, context_bytes=1,
        )
    assert ledger.status is ResearchTerminalState.BUDGET_EXHAUSTED
    assert ledger.promotion_allowed is False


def test_synthesis_can_use_reserved_capacity_and_failed_calls_count():
    ledger = ResearchLedger(budget())
    ledger.record_call(
        lane="collection", provider="fictional", success=False,
        tokens=600, cost_usd=6.0, context_bytes=6000,
    )
    ledger.record_call(
        lane="synthesis", provider="fictional", success=True,
        tokens=200, cost_usd=2.0, context_bytes=2000,
    )
    receipt = ledger.receipt()
    assert receipt["usage"]["calls"] == 2
    assert receipt["usage"]["tokens"] == 800
    assert receipt["lanes"]["collection"]["failed_calls"] == 1


def test_unknown_provider_usage_remains_unknown_not_zero():
    ledger = ResearchLedger(budget())
    ledger.record_call(
        lane="collection", provider="fictional", success=True,
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
            lane="evaluation", provider="fictional", success=True,
            tokens=1, cost_usd=0.0, context_bytes=1,
        )


def test_completed_run_can_promote():
    ledger = ResearchLedger(budget())
    ledger.finish(ResearchTerminalState.COMPLETED, stop_reason="completed")
    assert ledger.promotion_allowed is True
