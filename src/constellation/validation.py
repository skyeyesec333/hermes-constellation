"""Strict canonical Markdown validation."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ValidationError

from .frontmatter import FrontmatterError, parse_frontmatter
from .models import Analysis, Claim, Classification, Decision, EntityKind, EntityRecord, Event, Inquiry, Interaction, Observation, Opportunity, RelationshipRecord, ResearchRun, Snapshot, SourceItem, Watchlist
from .storage import safe_relative_path

CANONICAL_MODELS: dict[str, type[BaseModel]] = {
    "claims": Claim,
    "source-items": SourceItem,
    "entities": EntityRecord,
    "people": EntityRecord,
    "relationships": RelationshipRecord,
    "research": ResearchRun,
    "interactions": Interaction,
    "decisions": Decision,
    "inquiries": Inquiry,
    "opportunities": Opportunity,
    "analyses": Analysis,
    "classifications": Classification,
    "watchlists": Watchlist,
    "snapshots": Snapshot,
    "observations": Observation,
    "events": Event,
}
ALLOWED_CANONICAL_FOLDERS = frozenset(CANONICAL_MODELS)


class CanonicalValidationError(ValueError):
    pass


def canonical_model_for_path(relative_path: str | PurePosixPath) -> type[BaseModel]:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2 or path.suffix != ".md":
        raise CanonicalValidationError("canonical target must be a relative Markdown path")
    try:
        return CANONICAL_MODELS[path.parts[0]]
    except KeyError as exc:
        raise CanonicalValidationError("target folder is not canonical") from exc


def validate_canonical_text(text: str, relative_path: str | PurePosixPath) -> BaseModel:
    path = PurePosixPath(relative_path)
    model = canonical_model_for_path(path)
    try:
        metadata, body = parse_frontmatter(text)
    except FrontmatterError as exc:
        raise CanonicalValidationError(str(exc)) from exc
    if not body.strip():
        raise CanonicalValidationError("canonical note body cannot be empty")
    try:
        record = model.model_validate(metadata, strict=False)
    except ValidationError as exc:
        raise CanonicalValidationError("frontmatter does not match the canonical schema") from exc
    if path.parts[0] == "people" and (
        not isinstance(record, EntityRecord) or record.type is not EntityKind.PERSON
    ):
        raise CanonicalValidationError("people/ records must have type person")
    return record


def validate_evidence_excerpt(
    root: Path | str, source_ids: list[str], excerpt: str | None
) -> None:
    """Require an excerpt to be verbatim in at least one cited canonical source."""
    if not excerpt:
        raise CanonicalValidationError("claims require an evidence excerpt")
    vault = Path(root).absolute()
    source_bodies: list[str] = []
    for source_id in source_ids:
        path = safe_relative_path(vault, Path("source-items") / f"{source_id}.md")
        if not path.is_file() or path.is_symlink():
            raise CanonicalValidationError(f"inferred claim source item not found: {source_id}")
        try:
            _, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (FrontmatterError, UnicodeError, OSError) as exc:
            raise CanonicalValidationError(
                f"inferred claim source item is invalid: {source_id}"
            ) from exc
        source_bodies.append(body)
    if not any(excerpt in body for body in source_bodies):
        raise CanonicalValidationError(
            "claim evidence excerpt is not verbatim in any cited source"
        )


def validate_claim_evidence(root: Path | str, record: BaseModel) -> None:
    """Require inferred-claim excerpts to be verbatim in a cited canonical source."""
    if not isinstance(record, Claim) or record.claim_status.value != "inferred":
        return
    if not record.evidence_excerpt:
        raise CanonicalValidationError("inferred claims require an evidence excerpt")
    try:
        validate_evidence_excerpt(root, list(record.source_ids), record.evidence_excerpt)
    except CanonicalValidationError as exc:
        message = str(exc)
        if message == "claim evidence excerpt is not verbatim in any cited source":
            message = "inferred claim evidence excerpt is not verbatim in any cited source"
        raise CanonicalValidationError(message) from exc


def validate_vault(root: Path | str, *, limit: int = 100) -> dict[str, object]:
    """Validate canonical Markdown files and return a bounded machine-readable report."""
    vault = Path(root).absolute()
    bounded_limit = max(1, min(int(limit), 200))
    valid = 0
    invalid = 0
    errors: list[dict[str, str]] = []
    entity_bodies: dict[tuple[str, str], str] = {}
    for folder in sorted(ALLOWED_CANONICAL_FOLDERS):
        directory = vault / folder
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in sorted(directory.rglob("*.md")):
            if path.is_symlink() or not path.is_file():
                invalid += 1
                if len(errors) < bounded_limit:
                    errors.append({"path": path.relative_to(vault).as_posix(), "error": "unsafe file"})
                continue
            relative = path.relative_to(vault).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
                record = validate_canonical_text(text, relative)
                validate_claim_evidence(vault, record)
                valid += 1
                if (
                    isinstance(record, EntityRecord)
                    and record.status not in {"stale", "retired"}
                    and record.resolution_state.value != "merged"
                ):
                    _, body = parse_frontmatter(text)
                    normalized_body = body.strip()
                    if len(normalized_body) >= 128:
                        key = (
                            record.type.value,
                            sha256(normalized_body.encode("utf-8")).hexdigest(),
                        )
                        keeper_path = entity_bodies.get(key)
                        if keeper_path is None:
                            entity_bodies[key] = relative
                        else:
                            valid -= 1
                            invalid += 1
                            if len(errors) < bounded_limit:
                                errors.append({
                                    "path": relative,
                                    "error": (
                                        "active same-kind entity dossier body duplicates "
                                        f"{keeper_path}"
                                    ),
                                })
            except (CanonicalValidationError, UnicodeError, OSError) as exc:
                invalid += 1
                if len(errors) < bounded_limit:
                    errors.append({"path": relative, "error": str(exc)})
    return {
        "schema_version": "0.1",
        "valid": valid,
        "invalid": invalid,
        "errors": errors,
        "errors_truncated": invalid > len(errors),
    }
