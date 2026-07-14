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
