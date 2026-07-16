from constellation.meeting import (
    build_meeting_notes_map,
    build_meeting_transcript_map,
    reconcile_meeting_evidence,
)


def test_tactiq_style_transcript_keeps_timestamps_and_opaque_speaker_labels():
    result = build_meeting_transcript_map(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        text=(
            "Ada Example 00:00:05\n"
            "We will ship the pilot next week.\n"
            "Ben Example 00:01:12\n"
            "I can own the follow-up by Friday.\n"
        ),
    )

    assert result["status"] == "review-required"
    assert result["format"] == "tactiq"
    assert result["speaker_identities_inferred"] is False
    assert [segment["speaker_label"] for segment in result["segments"]] == [
        "Ada Example",
        "Ben Example",
    ]
    assert result["segments"][0]["start"] == "00:00:05"
    assert result["segments"][0]["anchor"] == "S0001"
    assert "pilot next week" in result["segments"][0]["text"]


def test_meetily_markdown_export_is_parsed_as_local_transcript_evidence():
    result = build_meeting_transcript_map(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        text=(
            "# Fictional Meeting\n\n"
            "## Transcript\n\n"
            "**Speaker 1** [00:00:10]: Market timing remains uncertain.\n"
            "**Speaker 2** [00:00:40]: Let's revisit after diligence.\n"
        ),
        format_hint="meetily",
    )

    assert result["format"] == "meetily"
    assert result["title"] == "Fictional Meeting"
    assert result["segments"][0]["speaker_label"] == "Speaker 1"
    assert result["segments"][0]["start"] == "00:00:10"
    assert result["speaker_identities_inferred"] is False


def test_openwhispr_style_export_preserves_confidence_gaps_without_inventing_speakers():
    result = build_meeting_transcript_map(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        text=(
            "[00:00:01.00 --> 00:00:04.50] (0.41) unclear audio about budget\n"
            "[00:00:05.00 --> 00:00:08.00] (0.93) confirm the next checkpoint\n"
        ),
        format_hint="openwhispr",
    )

    assert result["format"] == "openwhispr"
    assert result["segments"][0]["confidence"] == 0.41
    assert result["segments"][0]["low_confidence"] is True
    assert result["segments"][0]["speaker_label"] is None
    assert result["segments"][1]["low_confidence"] is False


def test_typed_notes_map_is_review_only_and_does_not_assert_decisions():
    result = build_meeting_notes_map(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        text="Decide pilot timeline\nAsk about budget owner\n",
    )

    assert result["status"] == "review-required"
    assert result["notes_assert_decisions"] is False
    assert [item["text"] for item in result["items"]] == [
        "Decide pilot timeline",
        "Ask about budget owner",
    ]
    assert result["items"][0]["anchor"] == "N0001"


def test_reconcile_surfaces_transcript_note_conflicts_without_canonical_writes():
    transcript = build_meeting_transcript_map(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        text="00:00:10 We approved the budget today.\n",
    )
    notes = build_meeting_notes_map(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        text="Budget was deferred pending diligence.\n",
    )

    result = reconcile_meeting_evidence(
        title="Fictional meeting",
        sources=[
            {"role": "tactiq-transcript", "map": transcript},
            {"role": "typed-notes", "map": notes},
        ],
    )

    assert result["status"] == "review-required"
    assert result["canonical_writes"] == []
    assert result["speaker_identities_inferred"] is False
    assert any("budget" in conflict["topic"] for conflict in result["conflicts"])
    assert len(result["member_source_ids"]) == 2


def test_meeting_transcript_ingest_records_review_only_segments(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from constellation.ingest import ExtractedSource, ingest_file
    from constellation.storage import sha256_bytes
    from constellation.vault import initialize_vault

    vault = tmp_path / "vault"
    initialize_vault(vault)
    source = vault / "Inbox/meeting.txt"
    data = b"Ada Example 00:00:05\nWe will ship the pilot next week.\n"
    source.write_bytes(data)
    extracted = ExtractedSource(
        data=data,
        text=data.decode("utf-8"),
        media_type="text/plain",
        extraction={
            "source_sha256": sha256_bytes(data),
            "status": "complete",
            "units": [{"index": 1, "anchor": "L0001", "status": "extracted", "method": "text"}],
        },
    )
    monkeypatch.setattr("constellation.ingest._read_source", lambda _: extracted)

    result = ingest_file(
        vault,
        "Inbox/meeting.txt",
        kind="meeting-transcript",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result["meeting_segments"] == "1"
    manifest = (vault / result["manifest_path"]).read_text(encoding="utf-8")
    assert '"speaker_identities_inferred": false' in manifest
    assert '"format": "tactiq"' in manifest
