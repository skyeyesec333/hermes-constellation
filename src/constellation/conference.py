"""Review-only conference encounter records from cards + meeting context."""

from __future__ import annotations

from datetime import date
from typing import Any


class ConferenceError(RuntimeError):
    """Raised when a conference encounter cannot be built safely."""


def _hint_from_card_fields(fields: list[dict[str, Any]]) -> dict[str, str | None]:
    email = None
    phone = None
    url = None
    unclassified: list[str] = []
    for field in fields:
        kind = str(field.get("field") or "")
        value = str(field.get("value") or "").strip()
        if not value:
            continue
        if kind == "email" and email is None:
            email = value
        elif kind == "phone" and phone is None:
            phone = value
        elif kind == "url" and url is None:
            url = value
        elif kind == "unclassified_text":
            unclassified.append(value)
    raw_name = unclassified[0] if unclassified else None
    company_hint = unclassified[1] if len(unclassified) > 1 else None
    return {
        "raw_name": raw_name,
        "company_hint": company_hint,
        "email": email,
        "phone": phone,
        "url": url,
    }


def build_conference_encounter(
    *,
    event_name: str,
    venue: str | None,
    event_date: date,
    project_title: str,
    card_fields: list[dict[str, Any]],
    conversation_summary: str | None,
    channel_preference: str,
    card_source_id: str,
    notes_source_id: str | None = None,
    where: str | None = None,
) -> dict[str, Any]:
    """Build a review-only encounter object. Never asserts a current role."""
    event_name = event_name.strip()
    project_title = project_title.strip()
    if not event_name or not project_title:
        raise ConferenceError("event name and project title are required")
    if channel_preference not in {"whatsapp", "sms", "email", "unknown"}:
        raise ConferenceError("unsupported channel preference")
    if not card_source_id:
        raise ConferenceError("card_source_id is required")

    person_hint = _hint_from_card_fields(card_fields)
    summary = (conversation_summary or "").strip()
    return {
        "version": 1,
        "kind": "conference-encounter",
        "status": "review-required",
        "event": {
            "name": event_name,
            "venue": (venue or "").strip() or None,
            "date": event_date.isoformat(),
            "project_title": project_title,
        },
        "person_hint": person_hint,
        "meeting_context": {
            "where": (where or "").strip() or None,
            "conversation_summary": summary or None,
            "promised": [],
            "tone": "warm",
            "channel_preference": channel_preference,
        },
        "source_ids": {
            "card": card_source_id,
            "notes": notes_source_id,
            "bundle_id": None,
        },
        "current_role_confirmed": False,
    }
