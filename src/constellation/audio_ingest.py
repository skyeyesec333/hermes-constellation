"""Local meeting-audio transcription without external APIs or speaker invention."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

Transcriber = Callable[..., dict[str, Any]]


class AudioIngestError(RuntimeError):
    """Raised when local audio intake cannot proceed safely."""


def _format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_audio_transcript_map(
    *,
    source_id: str,
    segments: list[dict[str, Any]],
    model_id: str,
    language: str | None,
    duration_seconds: float | None,
    device: str | None = None,
    compute_type: str | None = None,
) -> dict[str, Any]:
    """Normalize local transcription output into a review-only meeting transcript map."""
    mapped: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        confidence = segment.get("confidence")
        conf = float(confidence) if confidence is not None else None
        mapped.append(
            {
                "index": index,
                "anchor": f"S{index:04d}",
                "start": _format_timestamp(float(segment.get("start", 0.0))),
                "end": _format_timestamp(float(segment.get("end", segment.get("start", 0.0)))),
                "speaker_label": None,
                "text": str(segment.get("text", "")).strip(),
                "confidence": conf,
                "low_confidence": conf is not None and conf < 0.6,
            }
        )
    return {
        "version": 1,
        "status": "review-required",
        "source_id": source_id,
        "format": "local-audio",
        "speaker_identities_inferred": False,
        "duration_seconds": duration_seconds,
        "language": language,
        "model": {
            "id": model_id,
            "device": device,
            "compute_type": compute_type,
        },
        "segments": mapped,
    }


def _default_faster_whisper_transcriber(
    path: Path, *, model: str, language: str | None
) -> dict[str, Any]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise AudioIngestError(
            "faster-whisper is not installed; install with pip install -e '.[audio]'"
        ) from exc

    device = "cuda"
    compute_type = "float16"
    try:
        whisper = WhisperModel(model, device=device, compute_type=compute_type)
    except Exception:
        device = "cpu"
        compute_type = "int8"
        whisper = WhisperModel(model, device=device, compute_type=compute_type)

    segments_iter, info = whisper.transcribe(str(path), language=language, vad_filter=True)
    segments: list[dict[str, Any]] = []
    for item in segments_iter:
        avg_logprob = getattr(item, "avg_logprob", None)
        confidence = None
        if avg_logprob is not None:
            # Map logprob into a coarse 0-1 confidence without claiming calibration.
            confidence = max(0.0, min(1.0, 1.0 + float(avg_logprob)))
        segments.append(
            {
                "start": float(item.start),
                "end": float(item.end),
                "text": str(item.text).strip(),
                "confidence": confidence,
            }
        )
    return {
        "segments": segments,
        "language": getattr(info, "language", language),
        "duration_seconds": float(getattr(info, "duration", 0.0) or 0.0),
        "device": device,
        "compute_type": compute_type,
        "model_id": f"faster-whisper/{model}",
    }


def transcribe_audio(
    path: Path | str,
    *,
    source_id: str,
    confirmed: bool,
    model: str = "large-v3",
    language: str | None = None,
    transcriber: Transcriber | None = None,
) -> dict[str, Any]:
    """Transcribe a local recording only after explicit confirmation.

    No automatic recording, no remote transcription API, and no speaker diarization.
    """
    if not confirmed:
        raise AudioIngestError("recorded-conversation intake requires explicit confirmation")
    audio_path = Path(path)
    if audio_path.is_symlink() or not audio_path.is_file():
        raise AudioIngestError("audio source must be a regular non-symlink file")

    runner = transcriber or _default_faster_whisper_transcriber
    raw = runner(audio_path, model=model, language=language)
    return build_audio_transcript_map(
        source_id=source_id,
        segments=list(raw.get("segments") or []),
        model_id=str(raw.get("model_id") or f"faster-whisper/{model}"),
        language=raw.get("language"),
        duration_seconds=raw.get("duration_seconds"),
        device=raw.get("device"),
        compute_type=raw.get("compute_type"),
    )
