"""Review-only meeting evidence maps from local transcripts and notes.

Speaker labels present in exports are preserved as opaque strings only.
Constellation never invents speaker identities, decisions, or owners.
"""

from __future__ import annotations

import re
from typing import Any, Literal

MeetingFormat = Literal["tactiq", "meetily", "openwhispr", "generic", "notes"]

_TACTIQ_SPEAKER = re.compile(r"^(?P<label>.+?)\s+(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\s*$")
_MEETILY_LINE = re.compile(
    r"^\*\*(?P<label>[^*]+)\*\*\s*\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*:\s*(?P<text>.+)$"
)
_OPENWHISPR_LINE = re.compile(
    r"^\[(?P<start>\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*"
    r"(?:\((?P<conf>0?\.\d+|1(?:\.0+)?)\))?\s*(?P<text>.+)$"
)
_GENERIC_TS = re.compile(r"^(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<text>.+)$")
_TITLE_HEADING = re.compile(r"^#\s+(.+)$")
_LOW_CONFIDENCE = 0.6


def _normalize_timestamp(value: str) -> str:
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return f"00:{int(minutes):02d}:{int(float(seconds)):02d}"
    hours, minutes, seconds = parts
    return f"{int(hours):02d}:{int(minutes):02d}:{int(float(seconds)):02d}"


def _detect_format(text: str, format_hint: str | None) -> MeetingFormat:
    if format_hint in {"tactiq", "meetily", "openwhispr", "generic", "notes"}:
        return format_hint  # type: ignore[return-value]
    if any(_OPENWHISPR_LINE.fullmatch(line.strip()) for line in text.splitlines() if line.strip()):
        return "openwhispr"
    if any(_MEETILY_LINE.fullmatch(line.strip()) for line in text.splitlines() if line.strip()):
        return "meetily"
    if "## Transcript" in text or text.lstrip().startswith("# "):
        # Prefer meetily when export-style headings dominate and speaker lines exist later.
        for line in text.splitlines():
            if _MEETILY_LINE.fullmatch(line.strip()):
                return "meetily"
    speaker_hits = 0
    for line in text.splitlines():
        if _TACTIQ_SPEAKER.fullmatch(line.strip()):
            speaker_hits += 1
    if speaker_hits:
        return "tactiq"
    return "generic"


def _segment(
    *,
    index: int,
    text: str,
    start: str | None = None,
    end: str | None = None,
    speaker_label: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    low = confidence is not None and confidence < _LOW_CONFIDENCE
    return {
        "index": index,
        "anchor": f"S{index:04d}",
        "start": start,
        "end": end,
        "speaker_label": speaker_label,
        "text": text.strip(),
        "confidence": confidence,
        "low_confidence": low,
    }


def build_meeting_transcript_map(
    *,
    source_id: str,
    text: str,
    format_hint: str | None = None,
) -> dict[str, Any]:
    """Build a review-only transcript map from local export or typed transcript text."""
    fmt = _detect_format(text, format_hint)
    lines = text.splitlines()
    title: str | None = None
    segments: list[dict[str, Any]] = []

    if fmt == "meetily":
        current: dict[str, Any] | None = None
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            title_match = _TITLE_HEADING.fullmatch(line)
            if title_match and title is None:
                title = title_match.group(1).strip()
                continue
            if line.startswith("##"):
                continue
            meetily = _MEETILY_LINE.fullmatch(line)
            if meetily:
                if current is not None:
                    segments.append(current)
                groups = meetily.groupdict()
                current = _segment(
                    index=len(segments) + 1,
                    text=groups["text"],
                    start=_normalize_timestamp(groups["ts"]),
                    speaker_label=groups["label"].strip(),
                )
            elif current is not None:
                current["text"] = f"{current['text']} {line}".strip()
        if current is not None:
            segments.append(current)
    elif fmt == "openwhispr":
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            match = _OPENWHISPR_LINE.fullmatch(line)
            if match is None:
                continue
            conf = match.group("conf")
            confidence = float(conf) if conf is not None else None
            segments.append(
                _segment(
                    index=len(segments) + 1,
                    text=match.group("text"),
                    start=_normalize_timestamp(match.group("start")),
                    end=_normalize_timestamp(match.group("end")),
                    confidence=confidence,
                )
            )
    elif fmt == "tactiq":
        label: str | None = None
        start: str | None = None
        body: list[str] = []
        for raw in lines:
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            speaker = _TACTIQ_SPEAKER.fullmatch(stripped)
            if speaker:
                if body:
                    segments.append(
                        _segment(
                            index=len(segments) + 1,
                            text=" ".join(body),
                            start=start,
                            speaker_label=label,
                        )
                    )
                    body = []
                label = speaker.group("label").strip()
                start = _normalize_timestamp(speaker.group("ts"))
            else:
                body.append(stripped)
        if body:
            segments.append(
                _segment(
                    index=len(segments) + 1,
                    text=" ".join(body),
                    start=start,
                    speaker_label=label,
                )
            )
    else:
        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                continue
            generic = _GENERIC_TS.fullmatch(stripped)
            if generic:
                segments.append(
                    _segment(
                        index=len(segments) + 1,
                        text=generic.group("text"),
                        start=_normalize_timestamp(generic.group("ts")),
                    )
                )
            else:
                segments.append(_segment(index=len(segments) + 1, text=stripped))

    return {
        "version": 1,
        "status": "review-required",
        "source_id": source_id,
        "format": fmt,
        "title": title,
        "speaker_identities_inferred": False,
        "segments": segments,
    }


def build_meeting_notes_map(*, source_id: str, text: str) -> dict[str, Any]:
    """Build a review-only notes map. Notes are selective interpretation, not decisions."""
    items: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        items.append(
            {
                "index": len(items) + 1,
                "anchor": f"N{len(items) + 1:04d}",
                "text": line,
            }
        )
    return {
        "version": 1,
        "status": "review-required",
        "source_id": source_id,
        "format": "notes",
        "notes_assert_decisions": False,
        "items": items,
    }


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) >= 4}


def reconcile_meeting_evidence(
    *,
    title: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Surface conflicts across meeting evidence without writing canonical records."""
    if not sources:
        raise ValueError("meeting reconciliation requires at least one source")
    title = title.strip()
    if not title:
        raise ValueError("meeting title cannot be empty")

    member_ids: list[str] = []
    conflicts: list[dict[str, Any]] = []
    role_texts: dict[str, list[str]] = {}

    for source in sources:
        role = str(source.get("role", "unknown"))
        payload = source.get("map") or {}
        source_id = str(payload.get("source_id") or source.get("source_id") or "")
        if source_id:
            member_ids.append(source_id)
        texts: list[str] = []
        for segment in payload.get("segments") or []:
            texts.append(str(segment.get("text", "")))
        for item in payload.get("items") or []:
            texts.append(str(item.get("text", "")))
        role_texts.setdefault(role, []).extend(texts)

    # Conservative conflict heuristic: overlapping topical tokens with opposing cue words.
    oppose = {
        ("approved", "deferred"),
        ("approved", "rejected"),
        ("yes", "no"),
        ("agreed", "disagreed"),
        ("ship", "block"),
    }
    pairs = [
        (left_role, right_role)
        for left_role in role_texts
        for right_role in role_texts
        if left_role < right_role
    ]
    for left_role, right_role in pairs:
        left_blob = " ".join(role_texts[left_role]).lower()
        right_blob = " ".join(role_texts[right_role]).lower()
        shared = _tokens(left_blob) & _tokens(right_blob)
        for a, b in oppose:
            if (a in left_blob and b in right_blob) or (b in left_blob and a in right_blob):
                topic = next(iter(sorted(shared)), "meeting-content")
                conflicts.append(
                    {
                        "topic": topic,
                        "left_role": left_role,
                        "right_role": right_role,
                        "left_cue": a if a in left_blob else b,
                        "right_cue": b if a in left_blob else a,
                        "status": "unresolved",
                    }
                )

    return {
        "version": 1,
        "status": "review-required",
        "title": title,
        "member_source_ids": member_ids,
        "conflicts": conflicts,
        "canonical_writes": [],
        "speaker_identities_inferred": False,
        "roles": sorted(role_texts),
    }
