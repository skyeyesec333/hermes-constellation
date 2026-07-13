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
    source_url: str | None = None

    _original_path = field_validator("original_path")(_relative_posix)
    _text_path = field_validator("extracted_text_path")(
        lambda value: None if value is None else _relative_posix(value)
    )


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


RECORD_MODELS = (BaseRecord, SourceItem, Claim, CandidatePatch, ResearchRun)
