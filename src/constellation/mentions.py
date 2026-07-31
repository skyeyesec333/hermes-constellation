"""Evidence-anchored mention cross-reference (i2-successor Wave 2 Task 2.3).

A source mentioning two entities does NOT prove they are related. This
module produces three distinct, derived artifacts and never a canonical
fact:

1. ``MentionHit`` — derived source/entity reference with a deterministic
   char-offset anchor and match method;
2. co-mention leads — investigative leads only, excluded from canonical
   path/SNA by default and never auto-converted into relationships;
3. staged mention leads — review-only candidate packets
   (``kind: mention_candidate``) that cannot be promoted.

Matching is exact normalized title/alias matching with word boundaries and
longest-match-first span resolution. Warninglist suppression and
sensitivity exclusions are always reported, never silent. Output contains
no note body text.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .entity_warninglists import check_value, load_vault_warninglists
from .frontmatter import parse_frontmatter
from .models import generate_ulid
from .storage import atomic_write_text, safe_relative_path
from .vault import is_initialized

_MAX_TEXT_BYTES = 1_048_576
_SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
_RECEIPT_DIR = Path(".constellation/mention-scans")


class MentionError(RuntimeError):
    """Raised when a mention scan or staging fails closed."""


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _load_source(vault: Path, source_id: str) -> tuple[dict[str, Any], str, str]:
    path = vault / "source-items" / f"{source_id}.md"
    if path.is_symlink() or not path.is_file():
        raise MentionError(f"source not found: {source_id}")
    try:
        metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MentionError(f"source {source_id} is not parseable: {exc}") from exc
    if str(metadata.get("id", "")) != source_id:
        raise MentionError(f"source id mismatch for {source_id}")
    text = str(body)
    extracted = metadata.get("extracted_text_path")
    if extracted:
        try:
            extracted_path = safe_relative_path(vault, str(extracted))
        except Exception as exc:
            raise MentionError(f"source {source_id} has an unsafe extracted text path") from exc
        if extracted_path.is_file() and not extracted_path.is_symlink():
            raw = extracted_path.read_bytes()[: _MAX_TEXT_BYTES + 1]
            if len(raw) > _MAX_TEXT_BYTES:
                raise MentionError(f"source {source_id} extracted text exceeds the byte cap")
            text = raw.decode("utf-8", errors="replace")
    if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise MentionError(f"source {source_id} text exceeds the byte cap")
    return metadata, text, str(metadata.get("sensitivity", "internal"))


def _load_entities(vault: Path) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for folder in ("entities", "people"):
        base = vault / folder
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            record_id = str(metadata.get("id", ""))
            if not record_id or str(metadata.get("status", "")) in {"stale", "retired"}:
                continue
            entities.append(
                {
                    "id": record_id,
                    "title": str(metadata.get("title", "")),
                    "aliases": [str(item) for item in (metadata.get("aliases") or [])],
                    "kind": str(metadata.get("type", "")),
                    "sensitivity": str(metadata.get("sensitivity", "internal")),
                }
            )
    return entities


def _surface_index(entities: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Map normalized surface -> matching entity descriptors."""
    index: dict[str, list[dict[str, str]]] = {}
    for entity in entities:
        for method, surface in (
            ("title_exact", entity["title"]),
            *(("alias_exact", alias) for alias in entity["aliases"]),
        ):
            normalized = _normalize(surface)
            if not normalized:
                continue
            index.setdefault(normalized, []).append(
                {
                    "entity_id": entity["id"],
                    "kind": entity["kind"],
                    "sensitivity": entity["sensitivity"],
                    "method": method,
                    "surface": surface,
                }
            )
    return index


def scan_source_mentions(
    root: Path | str, source_id: str, *, limit: int = 200
) -> dict[str, Any]:
    """Scan one canonical source for entity mentions. Read-only except for
    the derived scan receipt under .constellation/mention-scans/."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise MentionError("vault is not initialized")
    if not 1 <= limit <= 1000:
        raise MentionError("limit must be between 1 and 1000")
    metadata, text, source_sensitivity = _load_source(vault, source_id)
    source_rank = _SENSITIVITY_RANK.get(source_sensitivity, 1)
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    entities = _load_entities(vault)
    index = _surface_index(entities)
    lists = load_vault_warninglists(vault)

    # Collect raw matches; resolve overlaps longest-match-first.
    raw_matches: list[tuple[int, int, str, list[dict[str, str]]]] = []
    for normalized, entries in index.items():
        sample = entries[0]["surface"]
        pattern = re.compile(r"(?<!\w)" + re.escape(sample) + r"(?!\w)", re.IGNORECASE)
        for found in pattern.finditer(text):
            raw_matches.append((found.start(), found.end(), normalized, entries))
    raw_matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    chosen: list[tuple[int, int, str, list[dict[str, str]]]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, normalized, entries in raw_matches:
        if any(start < taken_end and end > taken_start for taken_start, taken_end in occupied):
            continue
        occupied.append((start, end))
        chosen.append((start, end, normalized, entries))

    hits: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    excluded_by_sensitivity = 0
    for start, end, normalized, entries in chosen:
        visible = [
            entry
            for entry in entries
            if _SENSITIVITY_RANK.get(entry["sensitivity"], 3) <= source_rank
        ]
        hidden = len(entries) - len(visible)
        if hidden and not visible:
            excluded_by_sensitivity += hidden
            continue
        excluded_by_sensitivity += hidden
        surface = text[start:end]
        entity_ids = sorted({entry["entity_id"] for entry in visible})
        decision = check_value(surface, lists, entity_kind=visible[0]["kind"])
        anchor = f"chars {start}-{end}"
        if decision.decision == "suppress":
            suppressed.append(
                {
                    "entity_ids": entity_ids,
                    "surface": surface,
                    "anchor": anchor,
                    "reason": decision.reason,
                }
            )
            continue
        ambiguous = len(entity_ids) > 1 or decision.decision == "force_ambiguity"
        hits.append(
            {
                "entity_ids": entity_ids,
                "surface": surface,
                "anchor": anchor,
                "match_method": visible[0]["method"],
                "ambiguous": ambiguous,
                "ambiguity_reason": decision.reason if decision.decision == "force_ambiguity" else "",
            }
        )

    hits.sort(key=lambda hit: hit["anchor"])
    truncated = len(hits) > limit
    hits = hits[:limit]

    co_mentions: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    unique_entities = sorted({hit["entity_ids"][0] for hit in hits if not hit["ambiguous"]})
    for index_a, left in enumerate(unique_entities):
        for right in unique_entities[index_a + 1:]:
            pair = (left, right)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            co_mentions.append({"entity_ids": [left, right], "lead_only": True})

    receipt = {
        "version": 1,
        "source_id": source_id,
        "source_sha256": text_sha,
        "generated_at": datetime.now(UTC).isoformat(),
        "limit": limit,
        "hit_count": len(hits),
        "truncated": truncated,
        "ambiguous_count": sum(1 for hit in hits if hit["ambiguous"]),
        "suppressed_count": len(suppressed),
        "suppressed": suppressed,
        "excluded_by_sensitivity": excluded_by_sensitivity,
        "co_mention_count": len(co_mentions),
        "co_mentions": co_mentions,
        "hits": hits,
    }
    receipt_rel = _RECEIPT_DIR / f"mention-scan-{source_id}.json"
    atomic_write_text(vault, receipt_rel, json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    return {
        "status": "ok",
        "source_id": source_id,
        "source_sha256": text_sha,
        "hits": hits,
        "suppressed": suppressed,
        "co_mentions": co_mentions,
        "ambiguous_count": receipt["ambiguous_count"],
        "excluded_by_sensitivity": excluded_by_sensitivity,
        "truncated": truncated,
        "receipt_path": receipt_rel.as_posix(),
    }


def stage_mention_lead(
    root: Path | str,
    *,
    source_id: str,
    entity_id: str,
    anchor: str,
) -> dict[str, Any]:
    """Stage a review-only mention lead candidate. Never a relationship."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise MentionError("vault is not initialized")
    if not anchor.strip():
        raise MentionError("anchor is required")
    _load_source(vault, source_id)
    entity_ids = {entity["id"] for entity in _load_entities(vault)}
    if entity_id not in entity_ids:
        raise MentionError(f"entity not found: {entity_id}")
    packet = {
        "kind": "mention_candidate",
        "schema_version": "0.1",
        "id": generate_ulid(),
        "source_id": source_id,
        "entity_id": entity_id,
        "anchor": anchor.strip(),
        "status": "review-required",
        "created_at": datetime.now(UTC).isoformat(),
    }
    relative = Path(".constellation/candidates") / f"mention-{packet['id']}.json"
    atomic_write_text(vault, relative, json.dumps(packet, indent=2) + "\n")
    return {
        "status": "staged",
        "candidate_path": relative.as_posix(),
        "mention_id": packet["id"],
    }
