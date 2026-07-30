"""Stage 7.3 — contradiction detection + resolution proposals.

A contradiction is two or more LIVE claims with the same
(subject_id, predicate) but conflicting objects. Detection is pure read;
staging writes a REVIEW-ONLY candidate packet — nothing auto-resolves.

Proposals rank deterministic: source authority (claim_status band) first,
then support count (source_ids + supports), then recency. The human pick
happens at promotion: the proposed winner supersedes each loser through
the 7.1 edge — losers become stale, every edge ledgered, audit trail
complete. The model proposes; the human disposes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .models import generate_ulid
from .storage import atomic_write_text
from .vault import is_initialized

_AUTHORITY_BAND = {
    "corroborated": 4,
    "source-claimed": 3,
    "inferred": 2,
    "disputed": 1,
}
_EXCLUDED_STATES = {"stale", "superseded"}


class ContradictionError(RuntimeError):
    """Raised when contradiction work cannot proceed truthfully."""


def _parse_time(value: Any) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _live_claims(vault: Path) -> list[dict[str, Any]]:
    claims_dir = vault / "claims"
    records: list[dict[str, Any]] = []
    if not claims_dir.is_dir():
        return records
    for path in sorted(claims_dir.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(metadata.get("claim_status", "source-claimed")) in _EXCLUDED_STATES:
            continue
        records.append(metadata)
    return records


def _rank_claim(metadata: dict[str, Any]) -> dict[str, Any]:
    status = str(metadata.get("claim_status", "source-claimed"))
    support = len(metadata.get("source_ids") or []) + len(metadata.get("supports") or [])
    recency = max(
        _parse_time(metadata.get("observed_at")),
        _parse_time(metadata.get("updated_at")),
        _parse_time(metadata.get("created_at")),
    )
    return {
        "authority": status,
        "authority_score": _AUTHORITY_BAND.get(status, 0),
        "support_count": support,
        "recency": recency,
    }


def _object_key(metadata: dict[str, Any]) -> str:
    if metadata.get("object_id"):
        return f"id:{metadata['object_id']}"
    return f"lit:{metadata.get('object_literal', '')}"


def detect_contradictions(vault: Path | str) -> list[dict[str, Any]]:
    """Detect live same-subject+predicate claims with conflicting objects."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise ContradictionError("vault is not initialized")

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for metadata in _live_claims(vault):
        key = (str(metadata.get("subject_id", "")), str(metadata.get("predicate", "")))
        groups.setdefault(key, []).append(metadata)

    proposals: list[dict[str, Any]] = []
    for (subject_id, predicate), members in sorted(groups.items()):
        objects = {_object_key(m) for m in members}
        if len(objects) < 2:
            continue
        ranked = sorted(
            members,
            key=lambda m: (
                _rank_claim(m)["authority_score"],
                _rank_claim(m)["support_count"],
                _rank_claim(m)["recency"],
            ),
            reverse=True,
        )
        claims = [{
            "id": str(m.get("id", "")),
            "title": str(m.get("title", "")),
            "object": m.get("object_literal") or m.get("object_id"),
            "claim_status": str(m.get("claim_status", "source-claimed")),
            "rank_basis": _rank_claim(m),
            "path": f"claims/{m.get('id', '')}.md",
        } for m in ranked]
        proposals.append({
            "subject_id": subject_id,
            "predicate": predicate,
            "winner_id": claims[0]["id"],
            "loser_ids": [c["id"] for c in claims[1:]],
            "claims": claims,
        })
    return proposals


def stage_contradiction_candidate(
    vault: Path | str,
    subject_id: str,
    predicate: str,
    *,
    actor: str,
) -> dict[str, Any]:
    """Stage a review-only resolution proposal for one contradiction group.

    The packet records the proposed winner and losers; promotion (the human
    pick) applies 7.1 supersedes edges. Staging never mutates claims.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise ContradictionError("vault is not initialized")
    if not actor.strip():
        raise ContradictionError("actor is required")

    matching = [p for p in detect_contradictions(vault)
                if p["subject_id"] == subject_id and p["predicate"] == predicate]
    if not matching:
        raise ContradictionError(
            f"no contradiction for subject {subject_id} predicate {predicate!r}"
        )
    proposal = matching[0]
    candidate_id = generate_ulid()
    packet = {
        "kind": "contradiction_candidate",
        "id": candidate_id,
        "created_at": datetime.now(UTC).isoformat(),
        "actor": actor,
        **proposal,
    }
    relative = Path(".constellation/candidates") / f"contradiction-{candidate_id}.json"
    atomic_write_text(vault, relative, json.dumps(packet, indent=2, sort_keys=True) + "\n")
    return {
        "status": "staged",
        "candidate_id": candidate_id,
        "candidate_ref": f"contradiction-{candidate_id}",
        "winner_id": proposal["winner_id"],
        "loser_ids": proposal["loser_ids"],
        "claims": proposal["claims"],
    }
