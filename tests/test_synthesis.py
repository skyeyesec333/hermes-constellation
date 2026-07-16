from constellation.models import ResearchTerminalState
from constellation.research import BudgetExhausted, ResearchBudget
from constellation.synthesis import (
    SynthesisError,
    build_synthesis_plan,
    create_synthesis_run,
)


def test_card_profile_keeps_llm_budget_small_and_zero_token_extraction():
    plan = build_synthesis_plan(task_kind="business_card", source_bytes=40_000)

    assert plan["task_kind"] == "business_card"
    assert plan["phases"][0]["name"] == "preserve"
    assert plan["phases"][1]["name"] == "extract_or_transcribe"
    assert all(phase["llm_tokens"] == 0 for phase in plan["phases"] if phase["local"])
    assert plan["budget"]["max_model_calls"] <= 2
    assert plan["budget"]["evidence_packet_bytes"] <= 16_384
    assert plan["promotion_allowed"] is False


def test_meeting_audio_records_compute_separately_from_llm_tokens():
    plan = build_synthesis_plan(
        task_kind="meeting",
        source_bytes=1_000_000,
        estimated_audio_minutes=25.0,
    )
    audio_phase = next(phase for phase in plan["phases"] if phase["name"] == "extract_or_transcribe")
    assert audio_phase["llm_tokens"] == 0
    assert audio_phase["audio_minutes"] == 25.0
    assert audio_phase["compute"] == "local-transcription"


def test_book_prefers_hierarchical_maps_before_raw_segments():
    plan = build_synthesis_plan(
        task_kind="book",
        source_bytes=5_000_000,
        estimated_pages=700,
        derived_artifacts=[
            {"kind": "document-map", "content_sha256": "a" * 64},
            {"kind": "segment-index", "content_sha256": "b" * 64},
        ],
    )
    retrieve = next(phase for phase in plan["phases"] if phase["name"] == "retrieve")
    assert retrieve["strategy"] == "hierarchical-map-first"
    assert retrieve["prefer"] == ["document-map", "chapter-summary", "segment"]
    assert plan["requires_confirmation"] is True
    assert "explicit long-form mode" in plan["warnings"]


def test_competitive_analysis_reserves_contradiction_and_evaluation_budget():
    plan = build_synthesis_plan(task_kind="competitive_analysis", source_bytes=100_000)
    pressure = next(phase for phase in plan["phases"] if phase["name"] == "pressure_test")
    assert pressure["llm_tokens"] > 0
    assert plan["budget"]["contradiction_share"] >= 0.25
    assert plan["budget"]["synthesis_reserve"] >= 0.3


def test_cached_derived_artifacts_reduce_planned_calls_and_packet_stays_bounded():
    plan = build_synthesis_plan(
        task_kind="deck",
        source_bytes=200_000,
        derived_artifacts=[{"kind": "deck-map", "content_sha256": "c" * 64}],
    )
    cold = build_synthesis_plan(task_kind="deck", source_bytes=200_000, derived_artifacts=[])
    assert plan["estimated_model_calls"] < cold["estimated_model_calls"]
    assert plan["budget"]["evidence_packet_bytes"] <= 65_536


def test_synthesis_run_blocks_promotion_on_budget_exhaustion_and_records_gaps():
    run = create_synthesis_run(
        task_kind="business_card",
        source_bytes=10_000,
        provider="test-provider",
        model="test-model",
    )
    assert run.plan["promotion_allowed"] is False

    run.mark_local_phase("preserve", reused=False)
    run.mark_local_phase("extract_or_transcribe", reused=True, artifact_sha256="d" * 64)
    run.mark_local_phase("map_or_index", reused=True, artifact_sha256="e" * 64)

    # Force a tiny ledger budget so the next LLM call exhausts.
    run.ledger.budget = ResearchBudget(
        max_calls=1,
        max_tokens=100,
        max_cost_usd=0.01,
        max_context_bytes=1_000,
        synthesis_reserve=0.25,
    )
    try:
        run.record_llm_phase(
            phase="retrieve",
            lane="collection",
            tokens=50,
            cost_usd=0.001,
            context_bytes=200,
            source_ids=["01ARZ3NDEKTSV4RRFFQ69G5FAV"],
            packet_bytes=100,
        )
        run.record_llm_phase(
            phase="reason",
            lane="adjudication",
            tokens=50,
            cost_usd=0.001,
            context_bytes=200,
            source_ids=["01ARZ3NDEKTSV4RRFFQ69G5FAV"],
            packet_bytes=100,
        )
        exhausted = False
    except BudgetExhausted:
        exhausted = True
    assert exhausted is True
    receipt = run.finish_exhausted()
    assert receipt["status"] == ResearchTerminalState.BUDGET_EXHAUSTED.value
    assert receipt["promotion_allowed"] is False
    assert receipt["unresolved_gaps"]


def test_empty_retrieval_lanes_stop_after_two_no_delta_passes():
    run = create_synthesis_run(
        task_kind="meeting",
        source_bytes=50_000,
        provider="test-provider",
        model="test-model",
    )
    run.mark_local_phase("preserve", reused=False)
    run.mark_local_phase("extract_or_transcribe", reused=False)
    run.mark_local_phase("map_or_index", reused=False)
    run.record_llm_phase(
        phase="retrieve",
        lane="collection",
        tokens=10,
        cost_usd=0.001,
        context_bytes=100,
        source_ids=[],
        packet_bytes=0,
        evidence_delta=False,
    )
    run.record_llm_phase(
        phase="retrieve",
        lane="collection",
        tokens=10,
        cost_usd=0.001,
        context_bytes=100,
        source_ids=[],
        packet_bytes=0,
        evidence_delta=False,
    )
    assert run.should_stop_for_no_delta() is True
    receipt = run.finish_no_delta()
    assert receipt["promotion_allowed"] is False
    assert "no adjudicated evidence delta" in receipt["stop_reason"]


def test_oversized_evidence_packet_is_rejected():
    run = create_synthesis_run(
        task_kind="business_card",
        source_bytes=10_000,
        provider="test-provider",
        model="test-model",
    )
    run.mark_local_phase("preserve", reused=False)
    run.mark_local_phase("extract_or_transcribe", reused=False)
    run.mark_local_phase("map_or_index", reused=False)
    try:
        run.record_llm_phase(
            phase="retrieve",
            lane="collection",
            tokens=10,
            cost_usd=0.001,
            context_bytes=100,
            source_ids=["01ARZ3NDEKTSV4RRFFQ69G5FAV"],
            packet_bytes=run.plan["budget"]["evidence_packet_bytes"] + 1,
        )
        raised = False
    except SynthesisError:
        raised = True
    assert raised is True
