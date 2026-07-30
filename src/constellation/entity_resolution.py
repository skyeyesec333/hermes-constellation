"""Entity resolution + source-family dedup — review-gated, never auto-merge.

Auto-discovery produces duplicate canonical entities (an enriched dossier with
a discovery-artifact slug title plus an auto-created stub with the clean
title). This module detects those duplicates and stages the proven two-patch
merge recipe as review candidates; the owner promotes, nothing auto-merges.

Detection signals (same-kind pairs only; stale/merged records excluded):
- title_exact: slug-artifact-aware normalized titles are identical
- title_subset: one normalized title's tokens contain the other's (the
  artifact pair "billion fictional weave" vs "fictional weave"), Jaccard >= 0.5
- alias: a normalized alias equals the other record's normalized title/alias
- external_id: an identical (key, value) external identity pair

Normalization folds in the slug-artifact bug class ("company-billion-
company-..." discovery titles): leading kind tokens and trailing legal
suffixes are stripped so slug titles compare equal to human titles.

Source-family dedup is read-only reporting: source-items sharing a
normalized source_url or an identical source_hash are reported with the
earliest-created record as keeper. No mutation path — owner dispositions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .frontmatter import parse_frontmatter, render_frontmatter
from .identity import resolve_subject
from .models import (
    CandidatePatch,
    EntityKind,
    EntityRecord,
    Sensitivity,
    generate_ulid,
)
from .review import write_candidate
from .storage import sha256_file
from .vault import is_initialized


class EntityResolutionError(RuntimeError):
    """Raised when resolution input or vault state is unsafe/invalid."""


_KIND_PREFIXES = {
    "company", "person", "organization", "org", "place", "project",
    "concept", "strategy", "event",
}
_LEGAL_SUFFIXES = {
    "inc", "corp", "corporation", "llc", "ltd", "co", "company",
    "gmbh", "pte", "plc", "sa", "llp", "lp",
}
_SUBSET_MIN_JACCARD = 0.5


def normalize_entity_title(title: str) -> str:
    """Slug-artifact-aware title normalization for duplicate comparison."""
    normalized = unicodedata.normalize("NFKC", title).casefold()
    words = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).split()
    stripped = False
    while words and words[0] in _KIND_PREFIXES:
        words.pop(0)
        stripped = True
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
        stripped = True
    if stripped:
        # Slug-artifact titles repeat the kind token mid-slug
        # ("company-billion-company-acme-inc"). Only collapse the interior
        # "company" once the title has proven artifact form — a natural title
        # like "Fictional Company Store" keeps every token.
        words = [word for word in words if word != "company"]
    return " ".join(words)


@dataclass(frozen=True)
class DuplicateSignal:
    kind: str  # title_exact | title_subset | alias | external_id
    detail: str


@dataclass(frozen=True)
class EntityDuplicate:
    keeper_id: str
    stub_id: str
    entity_kind: str
    proposed_title: str
    keeper_path: str
    stub_path: str
    signals: tuple[DuplicateSignal, ...] = field(compare=True)


@dataclass(frozen=True)
class SourceFamilyDuplicate:
    basis: str  # source_url | source_hash
    family: str
    keeper_path: str
    duplicate_paths: tuple[str, ...]


@dataclass
class _EntityEntry:
    entity_id: str
    kind: EntityKind
    title: str
    normalized: str
    aliases: list[str]
    external_ids: dict[str, str]
    path: str
    richness: tuple[int, int, int]


def _require_vault(root: Path | str) -> Path:
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise EntityResolutionError("entity resolution requires an initialized vault")
    return vault


def _iter_folder_records(vault: Path, folder: str) -> list[tuple[Path, dict, str]]:
    base = vault / folder
    if not base.is_dir() or base.is_symlink():
        return []
    records = []
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue  # invalid records are validation's lane, not resolution's
        if isinstance(metadata, dict):
            records.append((path, metadata, body))
    return records


def _load_live_entities(vault: Path) -> list[_EntityEntry]:
    entries: list[_EntityEntry] = []
    for folder in ("entities", "people"):
        for path, metadata, body in _iter_folder_records(vault, folder):
            try:
                record = EntityRecord.model_validate(metadata, strict=False)
            except Exception:
                continue
            if record.status == "stale":
                continue
            if record.resolution_state.value == "merged":
                continue
            if folder == "people" and record.type is not EntityKind.PERSON:
                continue
            if folder == "entities" and record.type is EntityKind.PERSON:
                continue
            entries.append(_EntityEntry(
                entity_id=record.id,
                kind=record.type,
                title=record.title,
                normalized=normalize_entity_title(record.title),
                aliases=list(record.aliases),
                external_ids=dict(record.external_ids),
                path=path.relative_to(vault).as_posix(),
                richness=(
                    len(body),
                    len(record.aliases) + len(record.source_ids) + len(record.external_ids),
                    1 if record.resolution_state.value == "verified" else 0,
                ),
            ))
    return entries


def _pair_signals(left: _EntityEntry, right: _EntityEntry) -> list[DuplicateSignal]:
    signals: list[DuplicateSignal] = []
    if left.normalized and left.normalized == right.normalized:
        signals.append(DuplicateSignal(
            kind="title_exact",
            detail=f"normalized titles identical: {left.normalized!r}",
        ))
    elif left.normalized and right.normalized:
        left_tokens = set(left.normalized.split())
        right_tokens = set(right.normalized.split())
        smaller, larger = (
            (left_tokens, right_tokens)
            if len(left_tokens) <= len(right_tokens)
            else (right_tokens, left_tokens)
        )
        if smaller < larger:
            jaccard = len(smaller) / len(smaller | larger)
            if jaccard >= _SUBSET_MIN_JACCARD and any(
                len(token) >= 4 for token in smaller
            ):
                signals.append(DuplicateSignal(
                    kind="title_subset",
                    detail=(
                        f"title tokens {sorted(smaller)} contained in "
                        f"{sorted(larger)} (jaccard {jaccard:.2f})"
                    ),
                ))
    left_names = {left.normalized} | {normalize_entity_title(a) for a in left.aliases}
    right_names = {right.normalized} | {normalize_entity_title(a) for a in right.aliases}
    shared_names = {name for name in left_names & right_names if name}
    if shared_names and not any(s.kind == "title_exact" for s in signals):
        signals.append(DuplicateSignal(
            kind="alias",
            detail=f"shared normalized name/alias: {sorted(shared_names)}",
        ))
    shared_ids = {
        (key, value.casefold())
        for key, value in left.external_ids.items()
    } & {
        (key, value.casefold())
        for key, value in right.external_ids.items()
    }
    if shared_ids:
        signals.append(DuplicateSignal(
            kind="external_id",
            detail=f"shared external ids: {sorted(shared_ids)}",
        ))
    return signals


def _pick_keeper(left: _EntityEntry, right: _EntityEntry) -> tuple[_EntityEntry, _EntityEntry]:
    if left.richness != right.richness:
        return (left, right) if left.richness > right.richness else (right, left)
    return (left, right) if left.entity_id < right.entity_id else (right, left)


def _proposed_title(keeper: _EntityEntry, stub: _EntityEntry) -> str:
    def cleanliness(entry: _EntityEntry) -> tuple[int, int, str]:
        return (len(entry.normalized.split()), len(entry.title), entry.title)

    return min([keeper, stub], key=cleanliness).title


def scan_entity_duplicates(root: Path | str) -> list[EntityDuplicate]:
    """Read-only scan for duplicate canonical entities. Never writes."""
    vault = _require_vault(root)
    entries = sorted(_load_live_entities(vault), key=lambda entry: entry.entity_id)
    duplicates: list[EntityDuplicate] = []
    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            if left.kind != right.kind:
                continue
            signals = _pair_signals(left, right)
            if not signals:
                continue
            keeper, stub = _pick_keeper(left, right)
            duplicates.append(EntityDuplicate(
                keeper_id=keeper.entity_id,
                stub_id=stub.entity_id,
                entity_kind=keeper.kind.value,
                proposed_title=_proposed_title(keeper, stub),
                keeper_path=keeper.path,
                stub_path=stub.path,
                signals=tuple(signals),
            ))
    # Strongest evidence first for review triage; ids keep it deterministic.
    rank = {"title_exact": 0, "external_id": 1, "alias": 2, "title_subset": 3}
    duplicates.sort(key=lambda dup: (
        min(rank.get(signal.kind, 9) for signal in dup.signals),
        dup.keeper_id,
        dup.stub_id,
    ))
    return duplicates


def _normalize_source_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.casefold() or "https"
    host = parts.hostname.casefold() if parts.hostname else ""
    path = parts.path.rstrip("/")
    return f"{scheme}://{host}{path}"


def scan_source_family_duplicates(root: Path | str) -> list[SourceFamilyDuplicate]:
    """Read-only source-family dedup report: same normalized URL or same hash."""
    vault = _require_vault(root)
    by_url: dict[str, list[tuple[str, str]]] = {}
    by_hash: dict[str, list[tuple[str, str]]] = {}
    for path, metadata, _body in _iter_folder_records(vault, "source-items"):
        if metadata.get("status") == "stale":
            continue
        rel = path.relative_to(vault).as_posix()
        created = str(metadata.get("created_at", ""))
        url = metadata.get("source_url")
        if isinstance(url, str) and url.strip():
            by_url.setdefault(_normalize_source_url(url), []).append((created, rel))
        digest = metadata.get("source_hash")
        if isinstance(digest, str) and digest:
            by_hash.setdefault(digest, []).append((created, rel))

    families: list[SourceFamilyDuplicate] = []
    for basis, groups in (("source_url", by_url), ("source_hash", by_hash)):
        for family_key in sorted(groups):
            members = sorted(groups[family_key])  # earliest created first
            if len(members) < 2:
                continue
            families.append(SourceFamilyDuplicate(
                basis=basis,
                family=family_key,
                keeper_path=members[0][1],
                duplicate_paths=tuple(rel for _created, rel in members[1:]),
            ))
    return families


def _dedup_aliases(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            result.append(cleaned)
    return result


def _merged_stub_body(body: str, *, keeper_id: str, keeper_title: str, marker: str) -> str:
    note = (
        f"> MERGED {marker}: duplicate of [[{keeper_id}]] ({keeper_title}). "
        "This stub is retained for provenance; use the keeper record.\n"
    )
    lines = body.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("# "):
            return "".join(lines[: index + 1]) + "\n" + note + "".join(lines[index + 1:]).lstrip("\n")
    return note + "\n" + body


def stage_merge_proposal(
    root: Path | str,
    *,
    keeper_id: str,
    stub_id: str,
    proposed_title: str | None = None,
    extra_aliases: tuple[str, ...] = (),
) -> dict[str, object]:
    """Stage the two-patch merge recipe as review candidates. Never merges."""
    if keeper_id == stub_id:
        raise EntityResolutionError("keeper and stub must be different entities")
    vault = _require_vault(root)

    keeper = resolve_subject(vault, keeper_id)
    try:
        stub = resolve_subject(vault, stub_id)
    except Exception as exc:
        raise EntityResolutionError(f"stub entity not resolvable: {stub_id}") from exc

    if keeper.record.type != stub.record.type:
        raise EntityResolutionError(
            f"kind mismatch: keeper {keeper.record.type} vs stub {stub.record.type}"
        )
    if stub.record.status == "stale" or stub.record.resolution_state.value == "merged":
        raise EntityResolutionError(f"stub entity already resolved: {stub_id}")

    title = proposed_title or _proposed_title(
        _EntityEntry(keeper_id, keeper.record.type, keeper.record.title,
                     normalize_entity_title(keeper.record.title), [], {}, "", (0, 0, 0)),
        _EntityEntry(stub_id, stub.record.type, stub.record.title,
                     normalize_entity_title(stub.record.title), [], {}, "", (0, 0, 0)),
    )

    now = datetime.now(UTC)
    marker = now.date().isoformat()

    keeper_meta = dict(keeper.metadata)
    keeper_meta["title"] = title
    keeper_meta["aliases"] = _dedup_aliases([
        *keeper.record.aliases,
        *( [keeper.record.title] if keeper.record.title != title else [] ),
        *( [stub.record.title] if stub.record.title != title else [] ),
        *extra_aliases,
    ])
    keeper_patch = CandidatePatch(
        id=generate_ulid(),
        type="candidate-patch",
        title=f"Merge duplicate {keeper.record.type} entities: keeper gets clean title + aliases",
        status="candidate",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
        target_path=keeper.path.relative_to(vault).as_posix(),
        content=render_frontmatter(keeper_meta, keeper.body),
        expected_base_hash=sha256_file(keeper.path),
    )

    stub_meta = dict(stub.metadata)
    stub_meta["status"] = "stale"
    stub_patch = CandidatePatch(
        id=generate_ulid(),
        type="candidate-patch",
        title=f"Merge duplicate {keeper.record.type} entities: stub marked stale, points at keeper",
        status="candidate",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
        target_path=stub.path.relative_to(vault).as_posix(),
        content=render_frontmatter(
            stub_meta,
            _merged_stub_body(
                stub.body, keeper_id=keeper_id, keeper_title=title, marker=marker,
            ),
        ),
        expected_base_hash=sha256_file(stub.path),
    )

    keeper_candidate = write_candidate(vault, keeper_patch)
    stub_candidate = write_candidate(vault, stub_patch)
    return {
        "status": "staged",
        "keeper_id": keeper_id,
        "stub_id": stub_id,
        "keeper_candidate_id": keeper_patch.id,
        "stub_candidate_id": stub_patch.id,
        "keeper_candidate_path": keeper_candidate.relative_to(vault).as_posix(),
        "stub_candidate_path": stub_candidate.relative_to(vault).as_posix(),
        "proposed_title": title,
    }
