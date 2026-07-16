from constellation.cli import build_parser


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
