"""Conservative business-card OCR field extraction without identity or role assertions."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .identity import normalize_identity_email, normalize_identity_phone

_ANCHORED_LINE = re.compile(r"^\[([A-Z0-9:]+)]\s+(.+)$")
_HTTP_URL = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def _safe_url(value: str) -> str | None:
    if not _HTTP_URL.fullmatch(value):
        return None
    parsed = urlsplit(value)
    if not parsed.netloc or parsed.username or parsed.password:
        return None
    return value


def extract_business_card_fields(
    *,
    source_id: str,
    text: str,
    units: list[dict[str, Any]],
    phone_region: str | None = None,
) -> dict[str, Any]:
    """Return review-only contact evidence with OCR anchors; never infer a current role."""
    unit_by_anchor = {str(unit.get("anchor")): unit for unit in units}
    fields: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _ANCHORED_LINE.fullmatch(line.strip())
        if match is None:
            continue
        anchor, value = match.groups()
        value = value.strip()
        if not value:
            continue
        unit = unit_by_anchor.get(anchor, {})
        field = "unclassified_text"
        normalized_value = value
        email = normalize_identity_email(value)
        phone = normalize_identity_phone(value, region=phone_region)
        url = _safe_url(value)
        if email is not None:
            field, normalized_value = "email", email
        elif phone is not None:
            field, normalized_value = "phone", phone
        elif url is not None:
            field, normalized_value = "url", url
        fields.append(
            {
                "field": field,
                "value": normalized_value,
                "anchor": anchor,
                "confidence": unit.get("confidence"),
                "bounding_box": unit.get("bounding_box"),
            }
        )
    return {
        "version": 1,
        "status": "review-required",
        "source_id": source_id,
        "current_role_confirmed": False,
        "fields": fields,
    }
