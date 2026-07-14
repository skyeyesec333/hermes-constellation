"""Strict, versioned records for the Constellation core."""

from __future__ import annotations

import secrets
import time
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

SCHEMA_VERSION = "0.1"
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_ulid(timestamp_ms: int | None = None) -> str:
    """Generate a 26-character ULID using only the standard library."""
    timestamp = int(time.time_ns() // 1_000_000) if timestamp_ms is None else timestamp_ms
    if not 0 <= timestamp < 2**48:
        raise ValueError("ULID timestamp must fit in 48 bits")
    value = (timestamp << 80) | secrets.randbits(80)
    chars = ["0"] * 26
    for index in range(25, -1, -1):
        chars[index] = _ULID_ALPHABET[value & 31]
        value >>= 5
    return "".join(chars)


class EntityKind(StrEnum):
    PERSON = "person"
    COMPANY = "company"
    ORGANIZATION = "organization"
    PLACE = "place"
    PROJECT = "project"
    CONCEPT = "concept"
    STRATEGY = "strategy"
    EVENT = "event"
    OTHER = "other"


class EntityResolutionState(StrEnum):
    UNRESOLVED = "unresolved"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    MERGED = "merged"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ResearchTerminalState(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"
    CANCELLED = "cancelled"


Ulid = Annotated[str, Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _relative_posix(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not value or ".." in path.parts or "." in path.parts:
        raise ValueError("path must be a normalized relative POSIX path")
    return path.as_posix()


class BaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)

    schema_version: Literal["0.1"] = SCHEMA_VERSION
    id: Ulid = Field(default_factory=generate_ulid, frozen=True)
    type: Annotated[str, Field(min_length=1, max_length=100)]
    title: Annotated[str, Field(min_length=1, max_length=300)]
    status: Annotated[str, Field(min_length=1, max_length=100)]
    sensitivity: Sensitivity
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("record timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def updated_not_before_created(self) -> "BaseRecord":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class SourceItem(BaseRecord):
    source_hash: Sha256
    original_path: str
    media_type: Annotated[str, Field(min_length=1, max_length=100)]
    extracted_text_path: str | None = None
    extraction_manifest_path: str | None = None
    extraction_status: Literal["complete", "complete-with-gaps"] | None = None
    source_url: str | None = None

    _original_path = field_validator("original_path")(_relative_posix)
    _text_path = field_validator("extracted_text_path")(
        lambda value: None if value is None else _relative_posix(value)
    )
    _manifest_path = field_validator("extraction_manifest_path")(
        lambda value: None if value is None else _relative_posix(value)
    )


class EntityRecord(BaseRecord):
    type: EntityKind  # pyright: ignore[reportIncompatibleVariableOverride]
    aliases: list[str] = Field(default_factory=list, max_length=100)
    source_ids: list[Ulid] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    resolution_state: EntityResolutionState = EntityResolutionState.UNRESOLVED
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    merged_into: Ulid | None = None

    @field_validator("aliases")
    @classmethod
    def normalized_unique_aliases(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("entity aliases cannot be empty")
        if len({value.casefold() for value in cleaned}) != len(cleaned):
            raise ValueError("entity aliases must be unique")
        return cleaned

    @field_validator("external_ids")
    @classmethod
    def nonempty_external_ids(cls, values: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not value.strip() for key, value in values.items()):
            raise ValueError("external identity keys and values cannot be empty")
        return values

    @model_validator(mode="after")
    def evidence_and_merge_state_are_consistent(self) -> "EntityRecord":
        if self.resolution_state is EntityResolutionState.VERIFIED and not (
            self.source_ids or self.external_ids
        ):
            raise ValueError("verified entities require evidence")
        if self.resolution_state is EntityResolutionState.MERGED and self.merged_into is None:
            raise ValueError("merged entities require merged_into")
        if self.resolution_state is not EntityResolutionState.MERGED and self.merged_into is not None:
            raise ValueError("merged_into is only valid for merged entities")
        if self.merged_into == self.id:
            raise ValueError("an entity cannot merge into itself")
        return self


class RelationshipRecord(BaseRecord):
    type: Literal["relationship"] = "relationship"  # pyright: ignore[reportIncompatibleVariableOverride]
    subject_id: Ulid
    predicate: Annotated[str, Field(min_length=1, max_length=100)]
    object_id: Ulid
    source_ids: Annotated[list[Ulid], Field(min_length=1)]
    evidence_class: Literal["verified", "corroborated", "single-source", "inferred", "user-asserted"]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None = None

    @model_validator(mode="after")
    def endpoints_are_distinct(self) -> "RelationshipRecord":
        if self.subject_id == self.object_id:
            raise ValueError("relationship cannot relate to itself")
        return self


class Claim(BaseRecord):
    statement: Annotated[str, Field(min_length=1)]
    source_ids: Annotated[list[Ulid], Field(min_length=1)]


class CandidatePatch(BaseRecord):
    target_path: str
    content: Annotated[str, Field(min_length=1)]
    expected_base_hash: Sha256 | None = None

    _target_path = field_validator("target_path")(_relative_posix)


class ResearchRun(BaseRecord):
    stop_reason: str | None = None
    receipt: dict[str, object] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def require_terminal_status(cls, value: str) -> str:
        try:
            ResearchTerminalState(value)
        except ValueError as exc:
            raise ValueError("research run status must be terminal") from exc
        return value

    @computed_field
    @property
    def can_promote(self) -> bool:
        return self.status == ResearchTerminalState.COMPLETED.value

    @model_validator(mode="after")
    def receipt_matches_run(self) -> "ResearchRun":
        if not self.receipt:
            return self
        expected = {
            "version": 2,
            "run_id": self.id,
            "status": self.status,
            "promotion_allowed": self.can_promote,
        }
        for field, value in expected.items():
            if self.receipt.get(field) != value:
                raise ValueError(f"research receipt {field} does not match canonical run")
        if not self.receipt.get("finished_at"):
            raise ValueError("terminal research receipt requires finished_at")
        return self


RECORD_MODELS = (BaseRecord, SourceItem, EntityRecord, RelationshipRecord, Claim, CandidatePatch, ResearchRun)
