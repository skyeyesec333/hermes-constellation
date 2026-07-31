"""Warninglist-shaped entity disambiguation rules (i2-successor Wave 2 Task 2.2).

Independently authored against the behavior contract; inspired by MISP's
simple list contract (CC0) without copying its implementation. A warninglist
can suppress a candidate, force ambiguity, or require a specific entity
kind. Every suppression must appear in scan output — nothing is dropped
silently. Private operational entries live in the vault-local
``.constellation/entity-warninglists.json``, never in the public repo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_MATCH_TYPES = {"exact", "substring", "hostname", "regex"}
_ACTIONS = {"suppress", "force_ambiguity", "require_kind"}
_ENTITY_KINDS = {"person", "company", "organization", "place", "project", "concept"}
VAULT_LIST_RELATIVE = Path(".constellation/entity-warninglists.json")


class EntityWarninglistError(RuntimeError):
    """Raised when a warninglist file is missing or invalid."""


@dataclass(frozen=True)
class Warninglist:
    name: str
    version: int
    description: str
    match_attributes: tuple[str, ...]
    values: tuple[str, ...]
    action: str
    entity_kind: str | None


@dataclass(frozen=True)
class WarninglistHit:
    list_name: str
    matched_value: str
    match_type: str
    action: str
    entity_kind: str | None


@dataclass(frozen=True)
class WarningDecision:
    """Outcome of checking one candidate value against all lists."""

    decision: str  # pass | suppress | force_ambiguity
    reason: str
    hits: tuple[WarninglistHit, ...]


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _default_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "entity-resolution"
        / "default-warninglists.json"
    )


def _validate_list(raw: Any, *, source: str, index: int) -> Warninglist:
    if not isinstance(raw, dict):
        raise EntityWarninglistError(f"{source}: list {index} must be an object")
    name = str(raw.get("name", "")).strip()
    description = str(raw.get("description", "")).strip()
    match_attributes = raw.get("match_attributes")
    values = raw.get("values")
    action = str(raw.get("action", ""))
    entity_kind = raw.get("entity_kind")
    version = raw.get("version")
    if not name or not description:
        raise EntityWarninglistError(f"{source}: list {index} requires name and description")
    if not isinstance(version, int) or version < 1:
        raise EntityWarninglistError(f"{source}: list {name} requires a positive integer version")
    if (
        not isinstance(match_attributes, list)
        or not match_attributes
        or any(item not in _MATCH_TYPES for item in match_attributes)
    ):
        raise EntityWarninglistError(
            f"{source}: list {name} has unknown match_attributes"
        )
    if (
        not isinstance(values, list)
        or not values
        or len(values) > 500
        or any(not isinstance(item, str) or not item.strip() for item in values)
    ):
        raise EntityWarninglistError(f"{source}: list {name} has invalid values")
    if action not in _ACTIONS:
        raise EntityWarninglistError(f"{source}: list {name} has unknown action {action!r}")
    if action == "require_kind":
        if entity_kind not in _ENTITY_KINDS:
            raise EntityWarninglistError(
                f"{source}: list {name} requires entity_kind for require_kind"
            )
    elif entity_kind is not None:
        raise EntityWarninglistError(
            f"{source}: list {name} may set entity_kind only with require_kind"
        )
    if "regex" in match_attributes:
        for value in values:
            try:
                re.compile(value)
            except re.error as exc:
                raise EntityWarninglistError(
                    f"{source}: list {name} has invalid regex {value!r}"
                ) from exc
    return Warninglist(
        name=name,
        version=version,
        description=description,
        match_attributes=tuple(str(item) for item in match_attributes),
        values=tuple(str(item) for item in values),
        action=action,
        entity_kind=entity_kind,
    )


def _load_file(path: Path) -> list[Warninglist]:
    if not path.is_file() or path.is_symlink():
        raise EntityWarninglistError(f"warninglist file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EntityWarninglistError(f"warninglist file is invalid: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "0.1":
        raise EntityWarninglistError(f"warninglist file has unsupported schema: {path}")
    raw_lists = payload.get("lists")
    if not isinstance(raw_lists, list) or not raw_lists:
        raise EntityWarninglistError(f"warninglist file requires a non-empty lists array: {path}")
    return [_validate_list(item, source=path.name, index=i) for i, item in enumerate(raw_lists)]


def load_warninglists(
    path: Path | None = None, *, extra_paths: tuple[Path, ...] = ()
) -> tuple[Warninglist, ...]:
    """Load the default (or given) list file plus optional extras."""
    base = _load_file(Path(path) if path is not None else _default_path())
    lists = list(base)
    for extra in extra_paths:
        lists.extend(_load_file(Path(extra)))
    names = [item.name for item in lists]
    if len(set(names)) != len(names):
        raise EntityWarninglistError("warninglist names must be unique across files")
    return tuple(lists)


def load_vault_warninglists(vault: Path) -> tuple[Warninglist, ...]:
    """Default lists plus the vault-local operational file when present."""
    extra = Path(vault) / VAULT_LIST_RELATIVE
    extras = (extra,) if extra.is_file() and not extra.is_symlink() else ()
    return load_warninglists(extra_paths=extras)


def _host_of(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if "://" in text:
        host = urlparse(text).hostname
        return host.casefold() if host else None
    if "/" not in text and " " not in text and "." in text:
        return text.casefold()
    return None


def _matches(warning: Warninglist, raw_value: str) -> list[WarninglistHit]:
    hits: list[WarninglistHit] = []
    normalized = _normalize(raw_value)
    host = _host_of(raw_value)
    for match_type in warning.match_attributes:
        for candidate in warning.values:
            needle = _normalize(candidate)
            matched = False
            if match_type == "exact":
                matched = normalized == needle
            elif match_type == "substring":
                matched = bool(needle) and needle in normalized
            elif match_type == "hostname":
                candidate_host = candidate.strip().casefold()
                matched = host is not None and (
                    host == candidate_host or host.endswith(f".{candidate_host}")
                )
            elif match_type == "regex":
                matched = re.search(candidate, raw_value, re.IGNORECASE) is not None
            if matched:
                hits.append(
                    WarninglistHit(
                        list_name=warning.name,
                        matched_value=candidate,
                        match_type=match_type,
                        action=warning.action,
                        entity_kind=warning.entity_kind,
                    )
                )
    return hits


def check_value(
    value: str, lists: tuple[Warninglist, ...], *, entity_kind: str | None = None
) -> WarningDecision:
    """Check one candidate value (and optional entity kind) against the lists.

    require_kind lists suppress only when the candidate's kind differs from
    the required kind; matching kinds pass. suppress always suppresses.
    force_ambiguity keeps the candidate but demands human disambiguation.
    """
    hits: list[WarninglistHit] = []
    for warning in lists:
        hits.extend(_matches(warning, value))
    if not hits:
        return WarningDecision("pass", "", ())
    actionable: list[WarninglistHit] = []
    for hit in hits:
        if hit.action == "require_kind" and entity_kind == hit.entity_kind:
            continue  # kind pinned and satisfied
        actionable.append(hit)
    if not actionable:
        return WarningDecision("pass", "", tuple(hits))
    for hit in actionable:
        if hit.action == "suppress":
            return WarningDecision(
                "suppress",
                f"value matches suppress list {hit.list_name} ({hit.match_type}: {hit.matched_value})",
                tuple(hits),
            )
    for hit in actionable:
        if hit.action == "require_kind":
            return WarningDecision(
                "suppress",
                f"value requires entity kind {hit.entity_kind} per list {hit.list_name}",
                tuple(hits),
            )
    return WarningDecision(
        "force_ambiguity",
        f"value matches ambiguity list {actionable[0].list_name}; human disambiguation required",
        tuple(hits),
    )
