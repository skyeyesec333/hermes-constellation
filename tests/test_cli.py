import json

from constellation.cli import _parse_aware_timestamp, build_parser, run_action
from constellation.vault import initialize_vault


def test_cli_exposes_trusted_loop_commands():
    parser = build_parser()
    help_text = parser.format_help()

    assert "init" in help_text
    assert "doctor" in help_text
    assert "ingest" in help_text
    assert "validate" in help_text
    assert "index" in help_text
    assert "search" in help_text
    assert "review" in help_text
    assert "research" in help_text
    assert "migrate-plan" in help_text
    assert "migrate-rehearse" in help_text
    assert "migrate-prepare" in help_text
    assert "migrate-activate" in help_text


def test_extract_claims_cli_requires_provider_and_model_identity():
    values = vars(
        build_parser().parse_args(
            [
                "extract-claims",
                "/tmp/vault",
                "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "--subject-id",
                "01ARZ3NDEKTSV4RRFFQ69G5FAW",
                "--provider",
                "test-provider",
                "--model",
                "fictional-model-v1",
            ]
        )
    )

    assert values["provider"] == "test-provider"
    assert values["model"] == "fictional-model-v1"


def test_ingest_cli_accepts_local_capture_provenance_url():
    parser = build_parser()

    values = vars(
        parser.parse_args(
            [
                "ingest",
                "/tmp/vault",
                "/tmp/capture.txt",
                "--source-url",
                "https://example.test/capture",
            ]
        )
    )

    assert values["source_url"] == "https://example.test/capture"


def test_ingest_cli_accepts_business_card_routing_and_phone_region():
    values = vars(
        build_parser().parse_args(
            [
                "ingest",
                "/tmp/vault",
                "/tmp/card.png",
                "--kind",
                "business-card",
                "--phone-region",
                "US",
            ]
        )
    )

    assert values["kind"] == "business-card"
    assert values["phone_region"] == "US"


def test_ingest_cli_accepts_meeting_transcript_format_hint():
    values = vars(
        build_parser().parse_args(
            [
                "ingest",
                "/tmp/vault",
                "/tmp/meeting.md",
                "--kind",
                "meeting-transcript",
                "--meeting-format",
                "meetily",
            ]
        )
    )

    assert values["kind"] == "meeting-transcript"
    assert values["meeting_format"] == "meetily"


def test_optional_source_timestamp_is_not_replaced_with_operator_time():
    assert _parse_aware_timestamp(None, "--occurred-at") is None
    timestamp = _parse_aware_timestamp("2026-04-08T15:00:00-04:00", "--occurred-at")
    assert timestamp is not None
    assert timestamp.isoformat() == "2026-04-08T15:00:00-04:00"


def test_interaction_stage_uses_cli_enum_and_preserves_unknown_source_time(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    values = vars(
        build_parser().parse_args(
            [
                "interaction",
                str(vault),
                "stage",
                "--subject-ids",
                "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "--channel",
                "source-not-specified",
                "--summary",
                "Synthetic source-backed meeting.",
                "--source-ids",
                "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            ]
        )
    )

    result = run_action("interaction", values)
    payload = json.loads((vault / result["candidate_path"]).read_text(encoding="utf-8"))

    assert payload["interaction_type"] == "meeting"
    assert payload["occurred_at"] is None


def test_interaction_and_decision_cli_accept_source_timestamps():
    values = vars(
        build_parser().parse_args(
            [
                "interaction",
                "/tmp/vault",
                "stage",
                "--subject-ids",
                "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "--occurred-at",
                "2026-04-08T15:00:00-04:00",
            ]
        )
    )
    decision_values = vars(
        build_parser().parse_args(
            [
                "decision",
                "/tmp/vault",
                "stage",
                "--subject-id",
                "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "--decision",
                "Synthetic decision",
                "--decided-at",
                "2026-04-08T15:00:00-04:00",
            ]
        )
    )

    assert values["occurred_at"] == "2026-04-08T15:00:00-04:00"
    assert decision_values["decided_at"] == "2026-04-08T15:00:00-04:00"


def test_lead_cli_accepts_conference_capture_args():
    values = vars(
        build_parser().parse_args(
            [
                "lead",
                "/tmp/vault",
                "capture",
                "--event",
                "InfoComm Asia",
                "--date",
                "2026-07-21",
                "--project",
                "InfoComm Asia 2026 Leads",
                "--card",
                "Inbox/card.png",
                "--note",
                "Met near hall 3",
                "--channel",
                "whatsapp",
            ]
        )
    )
    assert values["event"] == "InfoComm Asia"
    assert values["project"] == "InfoComm Asia 2026 Leads"
    assert values["channel"] == "whatsapp"


def test_synthesize_cli_accepts_task_plan_args():
    values = vars(
        build_parser().parse_args(
            [
                "synthesize",
                "/tmp/vault",
                "plan",
                "--task",
                "book",
                "--source-bytes",
                "1000",
                "--pages",
                "20",
            ]
        )
    )
    assert values["task"] == "book"
    assert values["source_bytes"] == 1000
    assert values["pages"] == 20
