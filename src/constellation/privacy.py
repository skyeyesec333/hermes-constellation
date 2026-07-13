"""Local privacy checks for a candidate public release tree."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

_HOME_PATH = re.compile(r"/(?:home/[^\s/'\"]+|Users/[^\s/'\"]+|root)(?:/|\b)")
_WINDOWS_HOME_PATH = re.compile(r"\b[A-Z]:\\Users\\[^\s\\'\"]+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_POSSIBLE_SECRET = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|secret|password)\b\s*[:=]\s*['\"]?"
    r"[A-Z0-9_./+=-]{12,}",
    re.IGNORECASE,
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_KNOWN_TOKEN = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"
)
_BEARER_TOKEN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)
_E164_PHONE = re.compile(r"(?<!\w)\+[1-9]\d{7,14}(?!\d)")
_FORBIDDEN_NAMES = {".env", "auth.json", "cookies", "cookies.sqlite", "local state"}
_FORBIDDEN_SUFFIXES = {".sqlite", ".sqlite3", ".db"}


def _finding(path: Path, rule: str, detail: str) -> dict[str, str]:
    return {"path": path.as_posix(), "rule": rule, "detail": detail}


def audit_tree(root: Path, canaries: Iterable[str]) -> dict[str, Any]:
    """Scan paths and file bytes for common clean-room release leaks."""
    findings: list[dict[str, str]] = []
    root = Path(root)
    if root.is_symlink():
        findings.append(_finding(Path("."), "symlink-root", "Release root is a symlink"))
    root = root.resolve()
    if not root.is_dir():
        findings.append(_finding(Path("."), "missing-root", "Release root is not a directory"))
        return {
            "version": 2,
            "passed": False,
            "file_count": 0,
            "scanned_bytes": 0,
            "findings": findings,
        }
    canary_values = [value.encode("utf-8") for value in canaries if value]

    file_count = 0
    scanned_bytes = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            findings.append(_finding(relative, "symlink", "Release tree contains a symlink"))
            continue
        if not path.is_file():
            continue
        file_count += 1
        lowered_parts = {part.lower() for part in relative.parts}
        if (
            lowered_parts & _FORBIDDEN_NAMES
            or path.suffix.lower() in _FORBIDDEN_SUFFIXES
            or ".git" in lowered_parts
        ):
            findings.append(
                _finding(relative, "forbidden-artifact", "Release tree contains a private or derived artifact")
            )
        raw = path.read_bytes()
        scanned_bytes += len(raw)
        for canary in canary_values:
            if canary in raw:
                findings.append(_finding(relative, "release-canary", "Private canary found"))
        text = raw.decode("utf-8", errors="replace")
        if (
            _POSSIBLE_SECRET.search(text)
            or _PRIVATE_KEY.search(text)
            or _KNOWN_TOKEN.search(text)
            or _BEARER_TOKEN.search(text)
        ):
            findings.append(_finding(relative, "possible-secret", "Possible credential material found"))
        if _HOME_PATH.search(text) or _WINDOWS_HOME_PATH.search(text):
            findings.append(_finding(relative, "absolute-home-path", "Absolute user path found"))
        if _E164_PHONE.search(text):
            findings.append(_finding(relative, "phone", "Possible E.164 phone number found"))
        for email in _EMAIL.findall(text):
            if not email.lower().endswith(".example.test"):
                findings.append(_finding(relative, "email", "Non-example email found"))
        for address in _IPV4.findall(text):
            octets = [int(part) for part in address.split(".")]
            if all(0 <= part <= 255 for part in octets):
                findings.append(_finding(relative, "ipv4", "IPv4 address found"))

    return {
        "version": 2,
        "passed": not findings,
        "file_count": file_count,
        "scanned_bytes": scanned_bytes,
        "findings": findings,
    }
