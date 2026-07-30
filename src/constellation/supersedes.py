"""Stage 7.1 — supersedes links between claims.

A supersede is a journaled, reversible canonical mutation:

- the OLD claim is preserved with lifecycle state ``stale`` — never
  deleted, never hidden;
- the NEW claim carries the typed edge (``supersedes: [old_id]``);
- every application is appended to ``.constellation/supersedes-ledger.jsonl``
  with timestamp, actor, basis (source ULIDs or review id), and pre-write
  hashes — the rollback authority;
- reruns are idempotent (already-applied edges are reported, not rewritten);
- superseding an ALREADY-stale claim is refused unless ``force=True``, and
  force never writes directly — it stages a review candidate on the new
  claim for the normal review/promotion gate.

Both writes use expected-hash atomic writes (multi-writer safe). The chain
query walks the edge in both directions so "what changed about X" is
answerable from any link, cited by canonical path end to end.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter, render_frontmatter
from .models import CandidatePatch, Sensitivity
from .review import write_candidate
from .storage import atomic_write_text, sha256_file
from .vault import is_initialized

_LEDGER_REL = Path(".constellation/supersedes-ledger.jsonl")
_TERMINAL_STATES = {"stale", "superseded"}


class SupersedesError(RuntimeError):
    """Raised when a supersede operation cannot proceed truthfully."""


def _load_claim(vault: Path, claim_id: str) -> tuple[dict[str, Any], str, Path]:
    path = vault / "claims" / f"{claim_id}.md"
    if path.is_symlink() or not path.is_file():
        raise SupersedesError(f"claim not found: {claim_id}")
    try:
        metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SupersedesError(f"claim {claim_id} is not parseable: {exc}") from exc
    if str(metadata.get("id", "")) != claim_id:
        raise SupersedesError(
            f"claim id mismatch: path says {claim_id}, frontmatter says {metadata.get('id')!r}"
        )
    return metadata, body, path


def _claim_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(metadata.get("id", "")),
        "title": str(metadata.get("title", "")),
        "claim_status": str(metadata.get("claim_status", "source-claimed")),
        "predicate": str(metadata.get("predicate", "")),
        "object_literal": metadata.get("object_literal"),
        "object_id": metadata.get("object_id"),
        "supersedes": [str(item) for item in (metadata.get("supersedes") or [])],
        "path": f"claims/{metadata.get('id', '')}.md",
    }


def _append_ledger(vault: Path, entry: dict[str, Any]) -> None:
    ledger = vault / _LEDGER_REL
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def supersede_claim(
    vault: Path | str,
    new_claim_id: str,
    old_claim_id: str,
    *,
    actor: str,
    basis: list[str],
    force: bool = False,
) -> dict[str, Any]:
    """Mark old_claim_id stale and record the typed edge on new_claim_id."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise SupersedesError("vault is not initialized")
    if new_claim_id == old_claim_id:
        raise SupersedesError("a claim cannot supersede itself")
    if not actor.strip():
        raise SupersedesError("actor is required (who asserts this supersede)")
    if not basis:
        raise SupersedesError("basis is required (source ULIDs or review id)")

    old_meta, old_body, old_path = _load_claim(vault, old_claim_id)
    new_meta, new_body, new_path = _load_claim(vault, new_claim_id)

    old_status = str(old_meta.get("claim_status", "source-claimed"))
    new_links = [str(item) for item in (new_meta.get("supersedes") or [])]

    if old_status in _TERMINAL_STATES and old_claim_id in new_links:
        return {
            "status": "already_applied",
            "old_claim": _claim_summary(old_meta),
            "new_claim": _claim_summary(new_meta),
        }

    if old_status in _TERMINAL_STATES:
        if not force:
            raise SupersedesError(
                f"claim {old_claim_id} is already {old_status}; rerun with force "
                "to stage a review candidate instead of a direct write"
            )
        # Force never writes directly — stage a review candidate proposing the
        # edge on the NEW claim; the normal review gate decides.
        updated_new = dict(new_meta)
        updated_new["supersedes"] = new_links + [old_claim_id]
        updated_new["updated_at"] = datetime.now(UTC).isoformat()
        candidate = CandidatePatch(
            type="candidate_patch",
            title=f"Supersede already-{old_status} claim {old_claim_id} by {new_claim_id}",
            status="review-required",
            sensitivity=Sensitivity(str(new_meta.get("sensitivity", "internal"))),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            target_path=f"claims/{new_claim_id}.md",
            content=render_frontmatter(updated_new, new_body),
            expected_base_hash=sha256_file(new_path),
        )
        candidate_path = write_candidate(vault, candidate)
        return {
            "status": "staged_review",
            "candidate_id": candidate.id,
            "candidate_path": candidate_path.as_posix(),
            "old_claim": _claim_summary(old_meta),
            "new_claim": _claim_summary(new_meta),
        }

    old_hash = sha256_file(old_path)
    new_hash = sha256_file(new_path)
    now = datetime.now(UTC).isoformat()

    updated_old = dict(old_meta)
    updated_old["claim_status"] = "stale"
    updated_old["updated_at"] = now
    atomic_write_text(
        vault,
        old_path.relative_to(vault),
        render_frontmatter(updated_old, old_body),
        expected_hash=old_hash,
    )

    updated_new = dict(new_meta)
    updated_new["supersedes"] = new_links + [old_claim_id]
    updated_new["updated_at"] = now
    atomic_write_text(
        vault,
        new_path.relative_to(vault),
        render_frontmatter(updated_new, new_body),
        expected_hash=new_hash,
    )

    _append_ledger(vault, {
        "event": "supersede",
        "timestamp": datetime.now(UTC).isoformat(),
        "actor": actor,
        "basis": [str(item) for item in basis],
        "new_claim_id": new_claim_id,
        "old_claim_id": old_claim_id,
        "old_claim_hash": old_hash,
        "new_claim_hash": new_hash,
        "prior_old_status": old_status,
    })

    return {
        "status": "applied",
        "old_claim": _claim_summary(updated_old),
        "new_claim": _claim_summary(updated_new),
        "ledger": _LEDGER_REL.as_posix(),
    }


def supersede_chain(vault: Path | str, claim_id: str) -> dict[str, Any]:
    """Walk the supersedes chain containing claim_id, oldest to newest.

    Backward links come from each claim's ``supersedes`` edge; forward links
    are resolved by scanning canonical claims for edges pointing back.
    Cycles fail closed rather than looping.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise SupersedesError("vault is not initialized")
    anchor_meta, _, _ = _load_claim(vault, claim_id)

    # backward: what does this claim supersede (recursively)
    backward: list[str] = []
    visited = {claim_id}
    cursor = anchor_meta
    while True:
        links = [str(item) for item in (cursor.get("supersedes") or [])]
        if not links:
            break
        nxt = links[0]
        if nxt in visited:
            raise SupersedesError(f"supersedes cycle detected at {nxt}")
        visited.add(nxt)
        meta, _, _ = _load_claim(vault, nxt)
        backward.append(nxt)
        cursor = meta

    # forward: which canonical claim supersedes this one (recursively)
    forward: list[str] = []
    cursor_id = claim_id
    while True:
        successor = None
        claims_dir = vault / "claims"
        for path in sorted(claims_dir.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            links = [str(item) for item in (meta.get("supersedes") or [])]
            if cursor_id in links:
                successor = str(meta.get("id", ""))
                break
        if successor is None:
            break
        if successor in visited:
            raise SupersedesError(f"supersedes cycle detected at {successor}")
        visited.add(successor)
        forward.append(successor)
        cursor_id = successor

    ordered = list(reversed(backward)) + [claim_id] + forward
    entries = [_claim_summary(_load_claim(vault, cid)[0]) for cid in ordered]
    return {
        "anchor": claim_id,
        "head": ordered[-1],
        "length": len(ordered),
        "entries": entries,
    }
