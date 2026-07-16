from pathlib import Path

import pytest

from constellation.audio_ingest import AudioIngestError, build_audio_transcript_map, transcribe_audio


def test_audio_transcript_map_records_model_metadata_and_timestamped_segments():
    result = build_audio_transcript_map(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        segments=[
            {
                "start": 0.0,
                "end": 2.5,
                "text": "We will revisit the proposal.",
                "confidence": 0.88,
            },
            {
                "start": 2.5,
                "end": 5.0,
                "text": "unclear audio",
                "confidence": 0.2,
            },
        ],
        model_id="faster-whisper/large-v3",
        language="en",
        duration_seconds=5.0,
        device="cuda",
        compute_type="float16",
    )

    assert result["status"] == "review-required"
    assert result["speaker_identities_inferred"] is False
    assert result["model"]["id"] == "faster-whisper/large-v3"
    assert result["segments"][0]["start"] == "00:00:00"
    assert result["segments"][1]["low_confidence"] is True


def test_transcribe_audio_requires_explicit_confirmation_and_uses_injected_transcriber(tmp_path):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")

    def fake_transcriber(path: Path, *, model: str, language: str | None):
        assert path == audio
        assert model == "large-v3"
        return {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "Hello from local audio.", "confidence": 0.9}
            ],
            "language": language or "en",
            "duration_seconds": 1.0,
            "device": "cpu",
            "compute_type": "int8",
            "model_id": f"faster-whisper/{model}",
        }

    with pytest.raises(AudioIngestError):
        transcribe_audio(
            audio,
            source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            confirmed=False,
            transcriber=fake_transcriber,
        )

    result = transcribe_audio(
        audio,
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        confirmed=True,
        transcriber=fake_transcriber,
    )
    assert result["segments"][0]["text"] == "Hello from local audio."
    assert result["model"]["id"] == "faster-whisper/large-v3"
