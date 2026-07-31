"""Versioned relationship predicate registry (i2-successor Wave 1).

The registry is the single shared lookup for predicate identity, direction,
inverses, domain/range expectations, allowed qualifiers, and stability
classes. Enforcement is advisory: unknown legacy predicates remain readable
and produce warnings, never errors. Deprecation preserves old values and
proposes review-gated normalization; it never rewrites canonical files.

Confidence decay consumes the stability class through ``predicate_stability``
so the hard-coded predicate map is replaced by one shared lookup. Legacy
claim vocabulary that predates the registry (``pricing``, ``headquartered``,
...) is preserved in ``_LEGACY_CLAIM_PREDICATE_STABILITY`` so existing
decay behavior does not shift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .models import EntityKind, RelationshipRecord

REGISTRY_VERSION = 1

_STABILITY_CLASSES = {"durable", "standard", "transient"}
_QUALIFIER_KEY_LIMIT = 64

# Legacy claim predicate -> stability class, preserved verbatim from the
# pre-registry confidence module so existing decay behavior is unchanged.
# Relationship predicates resolve through the registry; this table is the
# compatibility fallback for claim-only vocabulary.
_LEGACY_CLAIM_PREDICATE_STABILITY = {
    "founded_in": "durable",
    "headquartered": "durable",
    "headquarters": "durable",
    "legal_name": "durable",
    "based_in": "durable",
    "architecture": "durable",
    "headcount": "transient",
    "pricing": "transient",
    "hiring": "transient",
    "job_opening": "transient",
    "status_update": "transient",
    "stock_price": "transient",
}


class PredicateRegistryError(RuntimeError):
    """Raised when a predicate registry is missing or violates invariants."""


@dataclass(frozen=True)
class PredicateEntry:
    name: str
    label: str
    inverse: str | None
    directed: bool
    symmetric: bool
    domains: tuple[str, ...]
    ranges: tuple[str, ...]
    stability: str
    allowed_qualifiers: tuple[str, ...]
    aliases: tuple[str, ...]
    external_mappings: dict[str, str] = field(default_factory=dict)
    deprecated_by: str | None = None


@dataclass(frozen=True)
class PredicateResolution:
    """Non-mutating canonicalization result for one predicate string."""

    status: str  # canonical | alias | deprecated | unknown
    requested: str
    canonical: str | None
    entry: PredicateEntry | None


@dataclass(frozen=True)
class SemanticFinding:
    severity: str  # error | warning
    code: str
    message: str


def _normalize(value: str) -> str:
    return value.strip().casefold()


class PredicateRegistry:
    """Immutable validated predicate registry."""

    def __init__(self, entries: tuple[PredicateEntry, ...], *, source: str) -> None:
        self.entries = entries
        self.source = source
        self._by_name = {entry.name: entry for entry in entries}
        self._aliases: dict[str, str] = {}
        for entry in entries:
            for alias in entry.aliases:
                self._aliases[_normalize(alias)] = entry.name

    def get(self, name: str) -> PredicateEntry | None:
        return self._by_name.get(_normalize(name))

    def resolve(self, value: str) -> PredicateResolution:
        normalized = _normalize(value)
        entry = self._by_name.get(normalized)
        if entry is not None:
            if entry.deprecated_by:
                target = self._by_name.get(entry.deprecated_by)
                return PredicateResolution("deprecated", value, entry.deprecated_by, target or entry)
            return PredicateResolution("canonical", value, entry.name, entry)
        target_name = self._aliases.get(normalized)
        if target_name is not None:
            target = self._by_name[target_name]
            if target.deprecated_by:
                deprecated_target = self._by_name.get(target.deprecated_by)
                return PredicateResolution(
                    "deprecated", value, target.deprecated_by, deprecated_target or target
                )
            return PredicateResolution("alias", value, target.name, target)
        return PredicateResolution("unknown", value, None, None)


def _known_entity_kinds() -> set[str]:
    return {kind.value for kind in EntityKind} | {"any"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PredicateRegistryError(message)


def _parse_entry(raw: dict[str, Any], *, index: int) -> PredicateEntry:
    _require(isinstance(raw, dict), f"predicate entry {index} must be a mapping")
    name = str(raw.get("name", "")).strip()
    _require(bool(name), f"predicate entry {index} requires a name")
    inverse = raw.get("inverse")
    inverse = None if inverse in (None, "", "null") else str(inverse)
    deprecated_by = raw.get("deprecated_by")
    deprecated_by = None if deprecated_by in (None, "", "null") else str(deprecated_by)
    stability = str(raw.get("stability", "standard"))
    _require(
        stability in _STABILITY_CLASSES,
        f"predicate {name}: stability must be one of {sorted(_STABILITY_CLASSES)}",
    )
    mappings = raw.get("external_mappings") or {}
    _require(isinstance(mappings, dict), f"predicate {name}: external_mappings must be a mapping")
    return PredicateEntry(
        name=name,
        label=str(raw.get("label", name)),
        inverse=inverse,
        directed=bool(raw.get("directed", True)),
        symmetric=bool(raw.get("symmetric", False)),
        domains=tuple(str(value) for value in (raw.get("domains") or ["any"])),
        ranges=tuple(str(value) for value in (raw.get("ranges") or ["any"])),
        stability=stability,
        allowed_qualifiers=tuple(str(value) for value in (raw.get("allowed_qualifiers") or [])),
        aliases=tuple(str(value) for value in (raw.get("aliases") or [])),
        external_mappings={str(key): str(value) for key, value in mappings.items()},
        deprecated_by=deprecated_by,
    )


def _validate_invariants(entries: tuple[PredicateEntry, ...]) -> None:
    names = [entry.name for entry in entries]
    _require(len(set(names)) == len(names), "predicate names must be unique")
    name_set = set(names)
    kinds = _known_entity_kinds()
    alias_owners: dict[str, str] = {}
    for entry in entries:
        for alias in entry.aliases:
            normalized = _normalize(alias)
            _require(
                normalized not in name_set,
                f"alias {alias!r} of {entry.name} collides with a canonical predicate name",
            )
            owner = alias_owners.get(normalized)
            _require(
                owner is None,
                f"alias {alias!r} is declared by both {owner} and {entry.name}",
            )
            alias_owners[normalized] = entry.name
        if entry.inverse is not None:
            _require(
                entry.inverse in name_set,
                f"predicate {entry.name}: inverse {entry.inverse!r} does not exist",
            )
        if entry.symmetric:
            _require(
                entry.inverse is None or entry.inverse == entry.name,
                f"symmetric predicate {entry.name} cannot have a different inverse",
            )
            _require(
                not entry.directed,
                f"symmetric predicate {entry.name} cannot also be directed",
            )
        for kind in (*entry.domains, *entry.ranges):
            _require(
                kind in kinds,
                f"predicate {entry.name}: unknown entity kind {kind!r} in domains/ranges",
            )
        if entry.deprecated_by is not None:
            _require(
                entry.deprecated_by in name_set,
                f"predicate {entry.name}: deprecated_by {entry.deprecated_by!r} does not exist",
            )
            _require(
                entry.deprecated_by != entry.name,
                f"predicate {entry.name} cannot deprecate itself",
            )


def load_predicate_registry(path: Path | None = None) -> PredicateRegistry:
    """Load and validate a predicate registry YAML file.

    ``path=None`` resolves the repository's ``resources/predicates/core.yaml``.
    Raises ``PredicateRegistryError`` when the file is missing, malformed, or
    violates registry invariants.
    """
    registry_path = Path(path) if path is not None else _default_registry_path()
    if not registry_path.is_file():
        raise PredicateRegistryError(f"predicate registry not found: {registry_path}")
    try:
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PredicateRegistryError(f"predicate registry is not valid YAML: {exc}") from exc
    _require(isinstance(raw, dict), "predicate registry root must be a mapping")
    _require(
        int(raw.get("version", 0)) == REGISTRY_VERSION,
        f"unsupported predicate registry version: {raw.get('version')!r}",
    )
    entries_raw = raw.get("predicates")
    _require(
        isinstance(entries_raw, list) and bool(entries_raw), "registry requires a predicates list"
    )
    entries = tuple(_parse_entry(item, index=i) for i, item in enumerate(entries_raw))
    _validate_invariants(entries)
    return PredicateRegistry(entries, source=str(registry_path))


def _default_registry_path() -> Path:
    return Path(__file__).resolve().parents[2] / "resources" / "predicates" / "core.yaml"


@lru_cache(maxsize=1)
def _default_registry() -> PredicateRegistry | None:
    try:
        return load_predicate_registry()
    except PredicateRegistryError:
        return None


def canonicalize_predicate(value: str, registry: PredicateRegistry) -> PredicateResolution:
    """Resolve a predicate string without mutating it.

    Reports ``canonical``, ``alias``, ``deprecated``, or ``unknown``; the
    caller decides what each status means for its context.
    """
    return registry.resolve(value)


def validate_relationship_semantics(
    record: RelationshipRecord,
    registry: PredicateRegistry,
    *,
    subject_kind: str | None = None,
    object_kind: str | None = None,
) -> list[SemanticFinding]:
    """Advisory semantic check of one relationship against the registry.

    Unknown legacy values warn; nothing here mutates the record. Endpoint
    kinds are optional because the record carries only IDs — callers that
    resolve endpoints (staging, validation) pass them for domain/range checks.
    """
    findings: list[SemanticFinding] = []
    resolution = registry.resolve(record.predicate)
    if resolution.status == "unknown":
        findings.append(
            SemanticFinding(
                "warning",
                "predicate_unknown",
                f"predicate {record.predicate!r} is not in the registry; kept readable in advisory mode",
            )
        )
        return findings
    if resolution.status == "alias":
        findings.append(
            SemanticFinding(
                "warning",
                "predicate_alias",
                f"predicate {record.predicate!r} is an alias of {resolution.canonical!r}",
            )
        )
    if resolution.status == "deprecated":
        findings.append(
            SemanticFinding(
                "warning",
                "predicate_deprecated",
                f"predicate {record.predicate!r} is deprecated; propose review-gated normalization to "
                f"{resolution.canonical!r}",
            )
        )
    entry = registry.get(record.predicate) or resolution.entry
    if entry is None:
        return findings
    if subject_kind and "any" not in entry.domains and subject_kind not in entry.domains:
        findings.append(
            SemanticFinding(
                "warning",
                "predicate_domain",
                f"subject kind {subject_kind!r} is outside domains {sorted(entry.domains)} for {entry.name}",
            )
        )
    if object_kind and "any" not in entry.ranges and object_kind not in entry.ranges:
        findings.append(
            SemanticFinding(
                "warning",
                "predicate_range",
                f"object kind {object_kind!r} is outside ranges {sorted(entry.ranges)} for {entry.name}",
            )
        )
    qualifiers = getattr(record, "qualifiers", None) or {}
    if entry.allowed_qualifiers:
        for key in qualifiers:
            if key not in entry.allowed_qualifiers:
                findings.append(
                    SemanticFinding(
                        "warning",
                        "qualifier_not_allowed",
                        f"qualifier {key!r} is not registered for predicate {entry.name}",
                    )
                )
    return findings


def predicate_stability(value: str, registry: PredicateRegistry | None = None) -> str:
    """Shared stability-class lookup for confidence decay.

    Resolution order: registry (canonical, alias, or deprecated target),
    then the preserved legacy claim table, then ``standard``. Never raises;
    a missing or unloadable default registry falls back to legacy behavior.
    """
    active = registry if registry is not None else _default_registry()
    if active is not None:
        resolution = active.resolve(value)
        if resolution.entry is not None:
            return resolution.entry.stability
    return _LEGACY_CLAIM_PREDICATE_STABILITY.get(_normalize(value), "standard")
