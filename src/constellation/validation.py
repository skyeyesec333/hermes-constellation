"""Strict canonical Markdown validation."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ValidationError

from .frontmatter import FrontmatterError, parse_frontmatter
from .models import Analysis, Claim, Decision, EntityRecord, Inquiry, Interaction, Opportunity, RelationshipRecord, ResearchRun, SourceItem

CANONICAL_MODELS: dict[str, type[BaseModel]] = {
    "claims": Claim,
    "source-items": SourceItem,
    "entities": EntityRecord,
    "relationships": RelationshipRecord,
    "research": ResearchRun,
    "interactions": Interaction,
    "decisions": Decision,
    "inquiries": Inquiry,
    "opportunities": Opportunity,
    "analyses": Analysis,
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
    model = canonical_model_for_path(relative_path)
    try:
        metadata, body = parse_frontmatter(text)
    except FrontmatterError as exc:
        raise CanonicalValidationError(str(exc)) from exc
    if not body.strip():
        raise CanonicalValidationError("canonical note body cannot be empty")
    try:
        return model.model_validate(metadata, strict=False)
    except ValidationError as exc:
        raise CanonicalValidationError("frontmatter does not match the canonical schema") from exc


def validate_vault(root: Path | str, *, limit: int = 100) -> dict[str, object]:
    """Validate canonical Markdown files and return a bounded machine-readable report."""
    vault = Path(root).absolute()
    bounded_limit = max(1, min(int(limit), 200))
    valid = 0
    invalid = 0
    errors: list[dict[str, str]] = []
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
                validate_canonical_text(path.read_text(encoding="utf-8"), relative)
                valid += 1
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
