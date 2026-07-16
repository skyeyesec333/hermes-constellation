"""Review-only conference encounter records from cards + meeting context."""

from __future__ import annotations

import re as _re
from datetime import date
from typing import Any


class ConferenceError(RuntimeError):
    """Raised when a conference encounter cannot be built safely."""


_NAME_ADDRESS_GARBAGE = _re.compile(
    r",\s*[A-Z][a-z]+\s*,|"         # comma-separated state/region: "Maine,Maryland"
    r"\d{5}|"                        # ZIP/postal code
    r"\b(?:Street|Road|Ave|Avenue|Blvd|Boulevard|Lane|Dr|Drive|Ct|Court|Way|Pl|Place)\b|"
    r"\bBangkok\b|"
    r"\bThailand\b|"
    r"\bSathorn\b",
    _re.IGNORECASE,
)


def _is_likely_address(value: str) -> bool:
    return bool(_NAME_ADDRESS_GARBAGE.search(value))


def _is_likely_person_name(value: str) -> bool:
    """Return True when `value` looks more like a person name than an address/region/company."""
    if _is_likely_address(value):
        return False
    # A person name: 1-4 words, each capitalized; may have a honorific prefix.
    stripped = value.strip().removesuffix(";").removesuffix(",")
    words = stripped.split()
    if not 1 <= len(words) <= 5:
        return False
    # Allow honorific prefixes: Mr., Ms., Mrs., Dr., Prof.
    honorifics = {"Mr.", "Mrs.", "Ms.", "Miss", "Dr.", "Prof.", "Mr", "Dr"}
    for word in words:
        if word in honorifics:
            continue
        if not word[0].isupper():
            return False
    return True


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

    # Prefer a likely person name over address/region junk.
    name_candidates = [v for v in unclassified if _is_likely_person_name(v)]
    non_name = [v for v in unclassified if not _is_likely_person_name(v)]
    raw_name = name_candidates[0] if name_candidates else (unclassified[0] if unclassified else None)
    # Company hint: prefer the second name-like candidate; otherwise, first non-name that isn't an address.
    company_hint: str | None = None
    if len(name_candidates) > 1:
        company_hint = name_candidates[1]
    elif non_name:
        # First non-address, non-name string — likely an org or title.
        org_candidates = [v for v in non_name if not _is_likely_address(v)]
        company_hint = org_candidates[0] if org_candidates else None

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
