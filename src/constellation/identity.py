"""Conservative, review-only identity suggestions for canonical entities."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from email_validator import EmailNotValidError, validate_email
import phonenumbers
from phonenumbers import NumberParseException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rapidfuzz import __version__ as RAPIDFUZZ_VERSION
from rapidfuzz.fuzz import ratio

from .frontmatter import FrontmatterError, parse_frontmatter
from .models import EntityKind, EntityRecord, Ulid
from .storage import safe_relative_path
from .vault import is_initialized

_COMPANY_SUFFIXES = frozenset({"co", "company", "corp", "corporation", "inc", "limited", "ltd", "llc", "plc"})
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class IdentityError(RuntimeError):
    """Raised when canonical entities cannot safely be compared."""


class SubjectResolutionError(RuntimeError):
    """Raised when a canonical subject cannot be resolved unambiguously."""


@dataclass(frozen=True)
class ResolvedSubject:
    """A validated canonical subject and its actual on-disk location."""

    path: Path
    record: EntityRecord
    metadata: dict[str, object]
    body: str


def _route_accepts(folder: str, kind: EntityKind) -> bool:
    if folder == "people":
        return kind is EntityKind.PERSON
    if folder == "entities":
        return kind is not EntityKind.PERSON
    return False


def resolve_subject(root: Path | str, subject_id: str) -> ResolvedSubject:
    """Resolve one subject ULID across canonical people/ and entities/ routes."""
    vault = Path(root).absolute()
    matches: list[ResolvedSubject] = []

    for folder in ("people", "entities"):
        base = vault / folder
        if not base.is_dir() or base.is_symlink():
            continue
        exact = base / f"{subject_id}.md"
        if exact.is_symlink():
            raise SubjectResolutionError(
                f"canonical subject path is a symlink: {subject_id}"
            )
        paths = ([exact] if exact.exists() else []) + [
            path for path in sorted(base.glob("*.md")) if path != exact
        ]
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            try:
                metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            except Exception as exc:
                if path == exact:
                    raise SubjectResolutionError(
                        f"canonical subject is invalid: {subject_id}"
                    ) from exc
                continue
            if not isinstance(metadata, dict):
                if path == exact:
                    raise SubjectResolutionError(
                        f"canonical subject is invalid: {subject_id}"
                    )
                continue
            candidate_id = str(metadata.get("id", ""))
            if candidate_id != subject_id:
                if path == exact:
                    raise SubjectResolutionError(
                        f"canonical subject filename does not match frontmatter id: {subject_id}"
                    )
                continue
            try:
                record = EntityRecord.model_validate(metadata, strict=False)
            except Exception as exc:
                raise SubjectResolutionError(
                    f"canonical subject is invalid: {subject_id}"
                ) from exc
            if not _route_accepts(folder, record.type):
                raise SubjectResolutionError(
                    f"canonical subject route does not match type: {subject_id}"
                )
            matches.append(
                ResolvedSubject(path=path, record=record, metadata=metadata, body=body)
            )

    if not matches:
        raise SubjectResolutionError(f"canonical subject not found: {subject_id}")
    if len(matches) != 1:
        raise SubjectResolutionError(f"canonical subject is ambiguous: {subject_id}")
    return matches[0]


def _candidate_id(left_entity_id: str, right_entity_id: str, entity_kind: EntityKind) -> str:
    digest = hashlib.sha256(f"{left_entity_id}:{right_entity_id}:{entity_kind}".encode()).digest()
    value = int.from_bytes(digest[:17], "big") >> 6
    characters = ["0"] * 26
    for index in range(25, -1, -1):
        characters[index] = _ULID_ALPHABET[value & 31]
        value >>= 5
    return "".join(characters)


class MatchFactor(BaseModel):
    """A visible comparison component behind an identity suggestion."""

    model_config = ConfigDict(extra="forbid", strict=True)

    field: Literal["name"]
    left_value: str
    right_value: str
    score: float = Field(ge=0.0, le=1.0)


class IdentityMatchCandidate(BaseModel):
    """A suggestion only; it carries no merge behavior."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_id: Ulid
    left_entity_id: Ulid
    right_entity_id: Ulid
    entity_kind: EntityKind
    score: float = Field(ge=0.0, le=1.0)
    factors: list[MatchFactor] = Field(min_length=1)
    status: Literal["pending"] = "pending"
    matcher: Literal["rapidfuzz"] = "rapidfuzz"
    matcher_version: str = RAPIDFUZZ_VERSION


def normalize_identity_email(value: str) -> str | None:
    """Return a local canonical email form without DNS or provider lookups."""
    try:
        return validate_email(value, check_deliverability=False).normalized.casefold()
    except EmailNotValidError:
        return None


def normalize_identity_phone(value: str, *, region: str | None) -> str | None:
    """Return E.164 only when the caller supplied an explicit parsing region."""
    try:
        number = phonenumbers.parse(value, region)
    except NumberParseException:
        return None
    if not phonenumbers.is_possible_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalize_identity_name(value: str, *, entity_kind: EntityKind) -> str:
    """Normalize a display name conservatively without changing the source record."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    words = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).split()
    if entity_kind in {EntityKind.COMPANY, EntityKind.ORGANIZATION}:
        while words and words[-1] in _COMPANY_SUFFIXES:
            words.pop()
    return " ".join(words)


def propose_identity_candidates(
    entities: list[EntityRecord], *, minimum_score: float = 0.9
) -> list[IdentityMatchCandidate]:
    """Return deterministic high-confidence suggestions; never mutate entities."""
    if not 0.0 <= minimum_score <= 1.0:
        raise ValueError("minimum_score must be between 0 and 1")
    candidates: list[IdentityMatchCandidate] = []
    for index, left in enumerate(sorted(entities, key=lambda entity: entity.id)):
        for right in sorted(entities, key=lambda entity: entity.id)[index + 1 :]:
            if left.type != right.type:
                continue
            left_name = normalize_identity_name(left.title, entity_kind=left.type)
            right_name = normalize_identity_name(right.title, entity_kind=right.type)
            if not left_name or not right_name:
                continue
            score = ratio(left_name, right_name) / 100
            if score < minimum_score:
                continue
            candidates.append(
                IdentityMatchCandidate(
                    candidate_id=_candidate_id(left.id, right.id, left.type),
                    left_entity_id=left.id,
                    right_entity_id=right.id,
                    entity_kind=left.type,
                    score=score,
                    factors=[
                        MatchFactor(
                            field="name",
                            left_value=left_name,
                            right_value=right_name,
                            score=score,
                        )
                    ],
                )
            )
    return candidates


def propose_identity_candidates_from_vault(root: Path | str) -> list[IdentityMatchCandidate]:
    """Read valid canonical entity records and return suggestions without writing."""
    if not is_initialized(root):
        raise IdentityError("identity resolution requires an initialized vault")
    directory = safe_relative_path(root, "entities")
    entities: list[EntityRecord] = []
    for path in sorted(directory.rglob("*.md")):
        if path.is_symlink() or not path.is_file():
            raise IdentityError("entity record is unsafe")
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            entities.append(EntityRecord.model_validate(metadata, strict=False))
        except (FrontmatterError, OSError, ValidationError) as exc:
            raise IdentityError("entity record is invalid") from exc
    return propose_identity_candidates(entities)
