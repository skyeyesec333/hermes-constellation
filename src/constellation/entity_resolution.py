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
- body_exact: byte-equivalent normalized dossier bodies (minimum 128 chars)

Normalization folds in the slug-artifact bug class ("company-billion-
company-..." discovery titles): leading kind tokens and trailing legal
suffixes are stripped so slug titles compare equal to human titles.

Source-family dedup is read-only reporting: source-items sharing a
normalized source_url or an identical source_hash are reported with the
earliest-created record as keeper. No mutation path — owner dispositions.
"""

from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
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
from .storage import atomic_write_text, safe_relative_path, sha256_file
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
    pair_id: str = ""
    review: "ResolutionReview | None" = None


@dataclass(frozen=True)
class ResolutionReview:
    decision: str
    reason: str
    reviewed_by: str
    reviewed_at: str


_DECISION_LEDGER = Path(".constellation/entity-resolution-decisions.json")


def entity_pair_id(left_id: str, right_id: str) -> str:
    """Return a deterministic, direction-independent entity-pair identifier."""
    left, right = sorted((left_id, right_id))
    return "entity-pair-" + sha256(f"{left}:{right}".encode("utf-8")).hexdigest()[:24]


def _load_distinct_decisions(vault: Path) -> dict[str, ResolutionReview]:
    path = safe_relative_path(vault, _DECISION_LEDGER)
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise EntityResolutionError("entity resolution decision ledger is unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EntityResolutionError("entity resolution decision ledger is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "0.1":
        raise EntityResolutionError("entity resolution decision ledger is invalid")
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        raise EntityResolutionError("entity resolution decision ledger is invalid")
    decisions: dict[str, ResolutionReview] = {}
    for item in raw_decisions:
        if not isinstance(item, dict):
            raise EntityResolutionError("entity resolution decision ledger is invalid")
        left_id = item.get("left_id")
        right_id = item.get("right_id")
        pair_id = item.get("pair_id")
        if (
            not isinstance(left_id, str)
            or not isinstance(right_id, str)
            or not isinstance(pair_id, str)
            or pair_id != entity_pair_id(left_id, right_id)
            or item.get("decision") != "distinct"
            or not isinstance(item.get("reason"), str)
            or not item["reason"].strip()
            or not isinstance(item.get("reviewed_by"), str)
            or not item["reviewed_by"].strip()
            or not isinstance(item.get("reviewed_at"), str)
        ):
            raise EntityResolutionError("entity resolution decision ledger is invalid")
        decisions[pair_id] = ResolutionReview(
            decision="distinct",
            reason=item["reason"].strip(),
            reviewed_by=item["reviewed_by"].strip(),
            reviewed_at=item["reviewed_at"],
        )
    return decisions


def record_distinct_decision(
    root: Path | str,
    *,
    left_id: str,
    right_id: str,
    reason: str,
    reviewed_by: str,
    reviewed_at: datetime,
) -> dict[str, str]:
    """Atomically upsert a reviewed-distinct decision in private runtime state."""
    vault = _require_vault(root)
    if left_id == right_id or not reason.strip() or not reviewed_by.strip():
        raise EntityResolutionError("distinct decision metadata is incomplete")
    if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
        raise EntityResolutionError("reviewed_at must include a timezone")
    left, right = sorted((left_id, right_id))
    pair_id = entity_pair_id(left, right)
    path = safe_relative_path(vault, _DECISION_LEDGER)
    existing: list[dict[str, str]] = []
    if path.exists():
        _load_distinct_decisions(vault)
        payload = json.loads(path.read_text(encoding="utf-8"))
        existing = [item for item in payload["decisions"] if item.get("pair_id") != pair_id]
    decision = {
        "pair_id": pair_id,
        "left_id": left,
        "right_id": right,
        "decision": "distinct",
        "reason": reason.strip(),
        "reviewed_by": reviewed_by.strip(),
        "reviewed_at": reviewed_at.isoformat(),
    }
    existing.append(decision)
    existing.sort(key=lambda item: item["pair_id"])
    atomic_write_text(
        vault,
        _DECISION_LEDGER,
        json.dumps(
            {"schema_version": "0.1", "decisions": existing},
            indent=2,
            sort_keys=True,
        ) + "\n",
    )
    return decision


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
    body_hash: str = ""


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
            if record.status in {"stale", "retired"}:
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
                body_hash=(
                    sha256(body.strip().encode("utf-8")).hexdigest()
                    if len(body.strip()) >= 128
                    else ""
                ),
            ))
    return entries


def _pair_signals(left: _EntityEntry, right: _EntityEntry) -> list[DuplicateSignal]:
    signals: list[DuplicateSignal] = []
    if left.body_hash and left.body_hash == right.body_hash:
        signals.append(DuplicateSignal(
            kind="body_exact",
            detail=f"normalized dossier bodies have identical sha256: {left.body_hash}",
        ))
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
    decisions = _load_distinct_decisions(vault)
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
            pair_id = entity_pair_id(keeper.entity_id, stub.entity_id)
            duplicates.append(EntityDuplicate(
                keeper_id=keeper.entity_id,
                stub_id=stub.entity_id,
                entity_kind=keeper.kind.value,
                proposed_title=_proposed_title(keeper, stub),
                keeper_path=keeper.path,
                stub_path=stub.path,
                signals=tuple(signals),
                pair_id=pair_id,
                review=decisions.get(pair_id),
            ))
    # Strongest evidence first for review triage; ids keep it deterministic.
    rank = {"body_exact": 0, "title_exact": 1, "external_id": 2, "alias": 3, "title_subset": 4}
    duplicates.sort(key=lambda dup: (
        min(rank.get(signal.kind, 9) for signal in dup.signals),
        dup.keeper_id,
        dup.stub_id,
    ))
    return duplicates


def scan_entity_duplicates_report(root: Path | str) -> dict[str, Any]:
    """Duplicate scan with warninglist triage applied, all visible.

    Suppressed pairs are reported (never silently dropped), ambiguity-listed
    pairs are flagged for human disambiguation but stay in the duplicate
    list, and clean pairs pass through unchanged.
    """
    from .entity_warninglists import check_value, load_vault_warninglists

    vault = _require_vault(root)
    lists = load_vault_warninglists(vault)
    kept: list[EntityDuplicate] = []
    suppressed: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for dup in scan_entity_duplicates(vault):
        decision = check_value(dup.proposed_title, lists, entity_kind=dup.entity_kind)
        if decision.decision == "suppress":
            suppressed.append({
                "pair_id": dup.pair_id,
                "proposed_title": dup.proposed_title,
                "entity_kind": dup.entity_kind,
                "reason": decision.reason,
            })
            continue
        if decision.decision == "force_ambiguity":
            ambiguous.append({
                "pair_id": dup.pair_id,
                "proposed_title": dup.proposed_title,
                "entity_kind": dup.entity_kind,
                "reason": decision.reason,
            })
        kept.append(dup)
    return {"duplicates": kept, "suppressed": suppressed, "ambiguous": ambiguous}


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
        if metadata.get("status") in {"stale", "superseded"}:
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


def _legacy_path_alias(path: Path, kind: EntityKind) -> str | None:
    """Recover a useful alias from a deterministic kind-prefixed legacy slug."""
    tokens = [token for token in re.split(r"[-_]+", path.stem.casefold()) if token]
    if len(tokens) < 3 or tokens[0] != kind.value:
        return None
    alias = " ".join(tokens[1:]).strip()
    return alias or None


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
    legacy_path_alias = _legacy_path_alias(stub.path, stub.record.type)
    keeper_meta["aliases"] = _dedup_aliases([
        *keeper.record.aliases,
        *( [keeper.record.title] if keeper.record.title != title else [] ),
        *( [stub.record.title] if stub.record.title != title else [] ),
        *( [legacy_path_alias] if legacy_path_alias else [] ),
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
    stub_meta["resolution_state"] = "merged"
    stub_meta["merged_into"] = keeper_id
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
