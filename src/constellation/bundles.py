"""Immutable, review-only bundles for compound evidence inputs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from .storage import atomic_write_text, safe_relative_path
from .vault import is_initialized

BundleKind = Literal["meeting", "deck", "business-card", "long-document"]
_ALLOWED_MEMBER_ROLES = frozenset(
    {
        "audio-original",
        "tactiq-transcript",
        "typed-notes",
        "handwritten-notes",
        "page-render",
        "derived-transcript",
        "source-original",
    }
)
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BundleError(RuntimeError):
    """Raised when compound evidence cannot be bundled safely."""


def _normalized_members(members: list[dict[str, str]]) -> list[dict[str, str]]:
    if not members:
        raise BundleError("evidence bundle requires at least one member")
    normalized: list[dict[str, str]] = []
    source_ids: set[str] = set()
    for member in members:
        if set(member) != {"source_id", "role", "sha256"}:
            raise BundleError("bundle members must contain only source_id, role, and sha256")
        source_id = member["source_id"]
        role = member["role"]
        digest = member["sha256"]
        if not _ULID.fullmatch(source_id) or not _SHA256.fullmatch(digest):
            raise BundleError("bundle member identifiers are invalid")
        if role not in _ALLOWED_MEMBER_ROLES:
            raise BundleError("bundle member role is not allowed")
        if source_id in source_ids:
            raise BundleError("evidence bundle cannot contain a source twice")
        source_ids.add(source_id)
        normalized.append({"source_id": source_id, "role": role, "sha256": digest})
    return normalized


def _bundle_id(kind: BundleKind, title: str, members: list[dict[str, str]]) -> str:
    payload = json.dumps(
        {"kind": kind, "title": title, "members": members}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def create_evidence_bundle(
    root: Path | str,
    *,
    kind: BundleKind,
    title: str,
    members: list[dict[str, str]],
) -> dict[str, Any]:
    """Create or return an immutable compound-evidence manifest without canonical writes."""
    if not is_initialized(root):
        raise BundleError("evidence bundles require an initialized vault")
    if kind not in {"meeting", "deck", "business-card", "long-document"}:
        raise BundleError("bundle kind is not allowed")
    title = title.strip()
    if not title:
        raise BundleError("bundle title cannot be empty")
    normalized = _normalized_members(members)
    bundle_id = _bundle_id(kind, title, normalized)
    relative = Path(".constellation/bundles") / f"{bundle_id}.json"
    path = safe_relative_path(root, relative)
    manifest = {
        "version": 1,
        "bundle_id": bundle_id,
        "kind": kind,
        "title": title,
        "members": normalized,
        "conflicts": [],
        "derived_artifacts": [],
        "canonical_candidates": [],
    }
    encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise BundleError("bundle destination is unsafe")
        if path.read_text(encoding="utf-8") != encoded:
            raise BundleError("existing bundle does not match its requested evidence")
    else:
        atomic_write_text(root, relative, encoded)
    return {"status": "created", "bundle_id": bundle_id, "path": relative.as_posix()}
