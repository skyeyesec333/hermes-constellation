"""Review-only claim extraction, staging, and promotion."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import Claim, ClaimStatus, Sensitivity, generate_ulid
from .storage import atomic_write_text, safe_relative_path
from .vault import is_initialized


class ClaimPipelineError(RuntimeError):
    """Raised when claim extraction or staging fails closed."""


def _claim_candidate_path(root: Path, claim_id: str) -> Path:
    return safe_relative_path(root, Path(".constellation/candidates") / f"claim-{claim_id}.json")


def stage_claim(
    root: Path | str,
    *,
    subject_id: str,
    predicate: str,
    object_id: str | None = None,
    object_literal: str | None = None,
    source_ids: list[str],
    evidence_anchor: str | None = None,
    evidence_excerpt: str | None = None,
    claim_status: str = "source-claimed",
    confidence: float | None = None,
    observed_at: datetime | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> dict[str, str]:
    """Stage a claim as a review-only candidate packet. Never auto-promotes."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise ClaimPipelineError("vault is not initialized")

    claim = Claim(
        type="claim",
        title=f"claim-{subject_id[:8]}-{predicate}",
        status="review-required",
        sensitivity=Sensitivity.INTERNAL,
        subject_id=subject_id,
        predicate=predicate,
        object_id=object_id,
        object_literal=object_literal,
        source_ids=source_ids,
        evidence_anchor=evidence_anchor,
        evidence_excerpt=evidence_excerpt,
        claim_status=ClaimStatus(claim_status),
        confidence=confidence,
        observed_at=observed_at,
        valid_from=valid_from,
        valid_to=valid_to,
        created_at=observed_at or datetime.now().astimezone(),
        updated_at=observed_at or datetime.now().astimezone(),
    )
    candidate_path = _claim_candidate_path(vault, claim.id)
    atomic_write_text(
        vault,
        candidate_path.relative_to(vault),
        claim.model_dump_json(indent=2) + "\n",
    )
    return {
        "status": "staged",
        "claim_id": claim.id,
        "candidate_path": candidate_path.relative_to(vault).as_posix(),
    }


def list_staged_claims(root: Path | str, *, limit: int = 50) -> list[dict[str, object]]:
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise ClaimPipelineError("vault is not initialized")
    candidates_dir = safe_relative_path(vault, ".constellation/candidates")
    results: list[dict[str, object]] = []
    for path in sorted(candidates_dir.glob("claim-*.json")):
        if len(results) >= limit:
            break
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("type") != "claim":
            continue
        results.append(
            {
                "id": payload.get("id"),
                "title": payload.get("title"),
                "subject_id": payload.get("subject_id"),
                "predicate": payload.get("predicate"),
                "claim_status": payload.get("claim_status"),
            }
        )
    return results
