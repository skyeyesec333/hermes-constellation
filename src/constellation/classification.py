"""Evidence-backed OSINT classification staging.

Classifications are review-required candidates. Staging validates that the
target entity and every referenced evidence record (claims, source items)
exist as valid canonical records. Referencing missing or malformed evidence
fails closed. Staging with zero evidence is permitted but is explicitly
marked ``unsupported`` so the review queue cannot mistake it for an
evidence-backed judgment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .frontmatter import parse_frontmatter
from .models import Classification, Claim, EntityRecord, Sensitivity, SourceItem, generate_ulid
from .storage import atomic_write_text
from .vault import is_initialized


class ClassificationError(RuntimeError):
    """Raised when classification staging fails closed."""


def _load_canonical(vault: Path, folder: str, record_id: str, not_found: str, invalid: str) -> dict:
    path = vault / folder / f"{record_id}.md"
    if not path.is_file() or path.is_symlink():
        raise ClassificationError(f"{not_found}: {record_id}")
    try:
        metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ClassificationError(f"{invalid}: {record_id}") from exc
    if not isinstance(metadata, dict) or metadata.get("id") != record_id:
        raise ClassificationError(f"{invalid}: {record_id}")
    return metadata


def _require_canonical_entity(vault: Path, entity_id: str) -> None:
    for folder in ("entities", "people"):
        path = vault / folder / f"{entity_id}.md"
        if path.is_file() and not path.is_symlink():
            try:
                metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
                record = EntityRecord.model_validate(metadata, strict=False)
            except Exception as exc:
                raise ClassificationError(f"canonical entity is invalid: {entity_id}") from exc
            if record.id != entity_id:
                raise ClassificationError(f"canonical entity is invalid: {entity_id}")
            return
    raise ClassificationError(f"canonical entity not found: {entity_id}")


def stage_classification(
    vault: Path | str,
    *,
    entity_id: str,
    category: str,
    methodology: str,
    rationale: str,
    supporting_claim_ids: list[str] | None = None,
    supporting_source_ids: list[str] | None = None,
    confidence: str = "medium",
) -> dict[str, object]:
    """Stage a review-required classification with validated evidence links."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise ClassificationError("vault is not initialized")

    _require_canonical_entity(vault, entity_id)

    claim_ids = [str(item) for item in (supporting_claim_ids or [])]
    source_ids = [str(item) for item in (supporting_source_ids or [])]
    for claim_id in claim_ids:
        metadata = _load_canonical(
            vault, "claims", claim_id,
            "canonical claim not found", "canonical claim is invalid",
        )
        try:
            Claim.model_validate(metadata, strict=False)
        except Exception as exc:
            raise ClassificationError(f"canonical claim is invalid: {claim_id}") from exc
    for source_id in source_ids:
        metadata = _load_canonical(
            vault, "source-items", source_id,
            "canonical source item not found", "canonical source item is invalid",
        )
        try:
            SourceItem.model_validate(metadata, strict=False)
        except Exception as exc:
            raise ClassificationError(f"canonical source item is invalid: {source_id}") from exc

    now = datetime.now(UTC)
    classification = Classification(
        id=generate_ulid(),
        title=f"{category.title()} classification for {entity_id}",
        status="review-required",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
        category=category,
        entity_id=entity_id,
        supporting_claim_ids=claim_ids,
        supporting_source_ids=source_ids,
        methodology=methodology,
        confidence=confidence,
        rationale=rationale,
        operator_reviewed=False,
    )
    candidate_rel = Path(".constellation/candidates") / f"classification-{classification.id}.json"
    atomic_write_text(vault, candidate_rel, classification.model_dump_json(indent=2) + "\n")
    return {
        "status": "staged",
        "classification_id": classification.id,
        "candidate_path": candidate_rel.as_posix(),
        "evidence_status": "evidence-backed" if (claim_ids or source_ids) else "unsupported",
    }
