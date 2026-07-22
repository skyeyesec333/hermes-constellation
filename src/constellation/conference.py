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
    r"\bUnit\s*\d*\b|"
    r"\bMezzanine\s+Floor\b|"
    r"\b(?:Corporate|Business)\s*(?:Center|Centre|Park)\b|"
    r"\bBangkok\b|"
    r"\bThailand\b|"
    r"\bSathorn\b",
    _re.IGNORECASE,
)


_NAME_INDUSTRY_TERMS = frozenset(
    {
        "energy",
        "solar",
        "tech",
        "group",
        "holdings",
        "international",
        "global",
        "solutions",
        "systems",
        "services",
        "capital",
        "partners",
        "ventures",
        "industries",
        "corporation",
        "limited",
        "trading",
        "enterprise",
        "marketing",
    }
)
_NAME_HONORIFICS = frozenset({"Mr.", "Mrs.", "Ms.", "Miss", "Dr.", "Prof.", "Mr", "Dr"})


def _is_likely_address(value: str) -> bool:
    return bool(_NAME_ADDRESS_GARBAGE.search(value))


def _is_likely_person_name(value: str) -> bool:
    """Return True when `value` looks more like a person name than an address/region/company."""
    if _is_likely_address(value):
        return False
    stripped = value.strip().removesuffix(";").removesuffix(",").removesuffix(".")
    words = stripped.split()
    if not 1 <= len(words) <= 5:
        return False
    # Reject company legal suffixes and acronyms.
    _COMPANY_SUFFIXES = frozenset(
        {"co.", "co", "ltd.", "ltd", "inc.", "inc", "corp.", "corp", "llc", "plc",
         "pty", "pte", "gmbh", "sarl", "nv", "bv", "sa", "spa", "kk", "sdn", "bhd"}
    )
    name_words: list[str] = []
    for word in words:
        if word in _NAME_HONORIFICS:
            continue
        normalized = word.strip(".,;")
        if (
            len(normalized) < 2
            or normalized.casefold() in _COMPANY_SUFFIXES
            or normalized.casefold() in _NAME_INDUSTRY_TERMS
            or not normalized[0].isupper()
        ):
            return False
        name_words.append(normalized)
    # A single token is too ambiguous for OCR lead identity; require review.
    return len(name_words) >= 2


def _name_confidence(value: str) -> float:
    """Score a candidate person name. Higher = more name-like. Not a percentage."""
    stripped = value.strip()
    words = stripped.split()
    score = 0.0
    # Favor shorter names (1-2 real-name words, 3 max with middle)
    if 2 <= len(words) <= 3:
        score += 0.3
    elif len(words) == 1:
        score += 0.1
    else:
        score -= 0.2
    # Favor things that look like given-name/surname pairs
    # (both short words, not industry terms like "Energy", "Solar", "Tech")
    _INDUSTRY_TERMS = frozenset(
        {"energy", "solar", "tech", "group", "holdings", "international", "global",
         "solutions", "systems", "services", "capital", "partners", "ventures",
         "industries", "corporation", "limited", "trading", "enterprise", "marketing"}
    )
    for word in words:
        if word.casefold() in _INDUSTRY_TERMS:
            score -= 0.4
    # Favor honorific-prefixed names
    honorifics = {"Mr.", "Mrs.", "Ms.", "Miss", "Dr.", "Prof.", "Mr", "Dr"}
    if words[0] in honorifics:
        score += 0.5
    # Favor typical person-name length (2-8 chars per word)
    for word in words:
        if 2 <= len(word) <= 10:
            score += 0.05
    return score


def _hint_from_card_fields(fields: list[dict[str, Any]]) -> dict[str, str | bool | None]:
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
    # Score candidates so "Jane Lee" beats "I-Solar Energy" (industry terms penalized).
    name_candidates = [v for v in unclassified if _is_likely_person_name(v)]
    if name_candidates:
        name_candidates.sort(key=_name_confidence, reverse=True)
    non_name = [v for v in unclassified if not _is_likely_person_name(v)]
    raw_name = name_candidates[0] if name_candidates else None
    name_review_required = raw_name is None
    # A company hint is allowed only alongside a credible person-name candidate.
    company_hint: str | None = None
    if name_candidates and len(name_candidates) > 1:
        company_hint = name_candidates[1]
    elif name_candidates and non_name:
        org_candidates = [v for v in non_name if not _is_likely_address(v)]
        company_hint = org_candidates[0] if org_candidates else None

    return {
        "raw_name": raw_name,
        "company_hint": company_hint,
        "name_review_required": name_review_required,
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
