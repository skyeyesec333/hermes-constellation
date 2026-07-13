"""Local privacy checks for a candidate public release tree."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

_HOME_PATH = re.compile(r"/(?:home|Users)/[^\s/'\"]+")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_POSSIBLE_SECRET = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|secret|password)\b\s*[:=]\s*['\"]?"
    r"[A-Z0-9_./+=-]{12,}",
    re.IGNORECASE,
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
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
        return {"version": 1, "passed": False, "findings": findings}
    canary_values = [value.encode("utf-8") for value in canaries if value]

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            findings.append(_finding(relative, "symlink", "Release tree contains a symlink"))
            continue
        if not path.is_file():
            continue
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
        for canary in canary_values:
            if canary in raw:
                findings.append(_finding(relative, "release-canary", "Private canary found"))
        text = raw.decode("utf-8", errors="replace")
        if _POSSIBLE_SECRET.search(text) or _PRIVATE_KEY.search(text):
            findings.append(_finding(relative, "possible-secret", "Possible credential material found"))
        if _HOME_PATH.search(text):
            findings.append(_finding(relative, "absolute-home-path", "Absolute user path found"))
        for email in _EMAIL.findall(text):
            if not email.lower().endswith(".example.test"):
                findings.append(_finding(relative, "email", "Non-example email found"))
        for address in _IPV4.findall(text):
            octets = [int(part) for part in address.split(".")]
            if all(0 <= part <= 255 for part in octets):
                findings.append(_finding(relative, "ipv4", "IPv4 address found"))

    return {"version": 1, "passed": not findings, "findings": findings}
