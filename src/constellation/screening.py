"""Stage 7.6 — ingest-time secret/PII screening.

Local, deterministic, zero-egress pre-ingest scan over extracted text.
Findings are reported by rule and severity only — a finding NEVER includes
the matched secret or PII itself. Content is never stripped or altered;
policy decides between block (strict default), quarantine-with-warning,
or off. Complements the release-time privacy audit (privacy.py); it does
not replace it.
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("known_token", re.compile(
        r"\b(?:AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9]{20,}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})\b"
    )),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)),
    ("secret_assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|token|aws_access_key_id)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{16,}"
    )),
)

_PII_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("phone", re.compile(r"(?<!\w)\+[1-9]\d{7,14}(?!\d)")),
    ("card_number", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
)


def screen_text(text: str) -> list[dict[str, Any]]:
    """Scan extracted text; return findings (rule + severity, never content)."""
    findings: list[dict[str, Any]] = []
    for rule, pattern in _SECRET_RULES:
        count = len(pattern.findall(text))
        if count:
            findings.append({"rule": rule, "severity": "secret", "matches": count})
    for rule, pattern in _PII_RULES:
        count = len(pattern.findall(text))
        if count:
            findings.append({"rule": rule, "severity": "pii", "matches": count})
    return findings
