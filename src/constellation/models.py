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

    # Optional CRM annotations written by `constellation crm apply`. These
    # mirror the opportunity-stage vocabulary and ISO date/datetime strings;
    # keeping them schema-valid is required for CRM writes to survive
    # canonical validation.
    stage: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    next_action: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    last_touch: Annotated[str, Field(min_length=1, max_length=64)] | None = None

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


class ClaimStatus(StrEnum):
    SOURCE_CLAIMED = "source-claimed"
    CORROBORATED = "corroborated"
    DISPUTED = "disputed"
    INFERRED = "inferred"
    SUPERSEDED = "superseded"
    STALE = "stale"


class Claim(BaseRecord):
    type: Literal["claim"] = "claim"  # pyright: ignore[reportIncompatibleVariableOverride]
    subject_id: Ulid
    predicate: Annotated[str, Field(min_length=1, max_length=100)]
    object_id: Ulid | None = None
    object_literal: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    source_ids: Annotated[list[Ulid], Field(min_length=1)]
    evidence_anchor: str | None = None
    evidence_excerpt: str | None = None
    claim_status: ClaimStatus = ClaimStatus.SOURCE_CLAIMED
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    contradicts: list[Ulid] = Field(default_factory=list)
    supports: list[Ulid] = Field(default_factory=list)
    supersedes: list[Ulid] = Field(default_factory=list)

    @model_validator(mode="after")
    def object_is_specified(self) -> "Claim":
        if self.object_id is None and self.object_literal is None:
            raise ValueError("claim requires object_id or object_literal")
        if self.object_id is not None and self.object_literal is not None:
            raise ValueError("claim cannot have both object_id and object_literal")
        return self

    @field_validator("observed_at", "valid_from", "valid_to")
    @classmethod
    def optional_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("claim temporal fields must include a timezone when set")
        return value

    @model_validator(mode="after")
    def valid_range_is_ordered(self) -> "Claim":
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot be earlier than valid_from")
        return self


class InteractionType(StrEnum):
    MEETING = "meeting"
    CALL = "call"
    EMAIL = "email"
    INTRODUCTION = "introduction"
    CONFERENCE = "conference"
    OTHER = "other"


class Interaction(BaseRecord):
    type: Literal["interaction"] = "interaction"  # pyright: ignore[reportIncompatibleVariableOverride]
    interaction_type: InteractionType = InteractionType.MEETING
    subject_ids: Annotated[list[Ulid], Field(min_length=1)]
    participants: list[Ulid] = Field(default_factory=list)
    channel: Annotated[str, Field(min_length=1, max_length=50)] = "in-person"
    summary: Annotated[str, Field(min_length=1)] = ""
    follow_ups: list[str] = Field(default_factory=list)
    decisions_made: list[Ulid] = Field(default_factory=list)
    source_ids: list[Ulid] = Field(default_factory=list)
    occurred_at: datetime | None = None
    location: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_requires_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("interaction occurred_at must include a timezone when set")
        return value


class Decision(BaseRecord):
    type: Literal["decision"] = "decision"  # pyright: ignore[reportIncompatibleVariableOverride]
    subject_id: Ulid
    decision: Annotated[str, Field(min_length=1)]
    rationale: str = ""
    options_considered: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    owner: str | None = None
    review_date: datetime | None = None
    outcome: str | None = None
    source_ids: list[Ulid] = Field(default_factory=list)
    decided_at: datetime | None = None

    @field_validator("review_date", "decided_at")
    @classmethod
    def decision_datetime_requires_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("decision temporal fields must include a timezone when set")
        return value


class Inquiry(BaseRecord):
    type: Literal["inquiry"] = "inquiry"  # pyright: ignore[reportIncompatibleVariableOverride]
    question: Annotated[str, Field(min_length=1)]
    why_it_matters: str = ""
    target_scope: str = ""
    evidence_needed: str = ""
    source_priority: str = "primary"
    promotion_policy: Literal["review-all", "auto-source-only", "manual-only"] = "review-all"
    subject_ids: list[Ulid] = Field(default_factory=list)
    max_search_queries: int = 5
    max_unique_sources: int = 10
    max_model_calls: int = 3
    max_pages: int = 5
    synthesis_reserve_percent: Annotated[int, Field(ge=0, le=50)] = 25
    stop_conditions: list[str] = Field(default_factory=list)
    research_run_ids: list[Ulid] = Field(default_factory=list)
    resolved_at: datetime | None = None
    resolution_summary: str | None = None


class SearchResult(BaseRecord):
    type: Literal["search-result"] = "search-result"  # pyright: ignore[reportIncompatibleVariableOverride]
    inquiry_id: Ulid
    query: Annotated[str, Field(min_length=1)]
    engine: Literal["searxng", "firecrawl", "other"] = "searxng"
    result_url: Annotated[str, Field(min_length=1)]
    result_title: str = ""
    result_snippet: str = ""
    retrieved_at: datetime
    source_preserved: bool = False
    source_id: Ulid | None = None


class OpportunityStage(StrEnum):
    TEST = "test"
    REVIEW = "review"
    QUALIFYING = "qualifying"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    CLOSED_WON = "closed-won"
    CLOSED_LOST = "closed-lost"
    ON_HOLD = "on-hold"


class Opportunity(BaseRecord):
    type: Literal["opportunity"] = "opportunity"  # pyright: ignore[reportIncompatibleVariableOverride]
    subject_ids: Annotated[list[Ulid], Field(min_length=1)]
    stage: OpportunityStage = OpportunityStage.TEST
    probability: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    expected_value: str | None = None
    next_action: str = ""
    next_action_due: datetime | None = None
    feeding_interactions: list[Ulid] = Field(default_factory=list)
    supporting_claims: list[Ulid] = Field(default_factory=list)
    supporting_decisions: list[Ulid] = Field(default_factory=list)
    source_ids: list[Ulid] = Field(default_factory=list)
    kanban_card_path: str | None = None

    @field_validator("next_action_due")
    @classmethod
    def due_date_requires_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("opportunity next_action_due must include a timezone when set")
        return value


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


class Analysis(BaseRecord):
    """Strategic analysis artifact — output from a framework run."""
    type: Literal["analysis"] = "analysis"  # pyright: ignore[reportIncompatibleVariableOverride]
    framework: Annotated[str, Field(min_length=1, max_length=100)]
    entity_id: Ulid
    agent_profile: str = ""
    supporting_claims: list[Ulid] = Field(default_factory=list)
    research_inquiries_spawned: list[Ulid] = Field(default_factory=list)
    chain_position: int | None = None
    chain_id: Ulid | None = None
    confidence: str = "medium"  # low, medium, high
    operator_reviewed: bool = False
    version: int = 1

    @field_validator("confidence")
    @classmethod
    def require_valid_confidence(cls, value: str) -> str:
        if value not in {"low", "medium", "high"}:
            raise ValueError("confidence must be low, medium, or high")
        return value


class EntityCategory(StrEnum):
    BUYER = "buyer"
    PARTNER = "partner"
    CHANNEL = "channel"
    COMPETITOR = "competitor"
    FALSE_LEAD = "false_lead"


class Classification(BaseRecord):
    """OSINT classification judgment linking an entity to a category with evidence."""

    type: Literal["classification"] = "classification"  # pyright: ignore[reportIncompatibleVariableOverride]
    category: Annotated[str, Field(min_length=1, max_length=50)]
    entity_id: Ulid
    supporting_claim_ids: list[Ulid] = Field(default_factory=list)
    supporting_source_ids: list[Ulid] = Field(default_factory=list)
    methodology: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: str = "medium"  # low, medium, high
    rationale: Annotated[str, Field(min_length=1, max_length=5000)]
    operator_reviewed: bool = False
    version: int = 1

    @field_validator("category")
    @classmethod
    def require_valid_category(cls, value: str) -> str:
        try:
            EntityCategory(value)
        except ValueError as exc:
            raise ValueError(
                "category must be one of: buyer, partner, channel, competitor, false_lead"
            ) from exc
        return value

    @field_validator("confidence")
    @classmethod
    def require_valid_confidence(cls, value: str) -> str:
        if value not in {"low", "medium", "high"}:
            raise ValueError("confidence must be low, medium, or high")
        return value


class Watchlist(BaseRecord):
    """A monitored entity or topic list with configuration."""

    type: Literal["watchlist"] = "watchlist"  # pyright: ignore[reportIncompatibleVariableOverride]
    entity_ids: list[Ulid] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)  # e.g. gdelt, edgar, polymarket
    schedule: str = ""  # cron-like schedule description
    version: int = 1


class Snapshot(BaseRecord):
    """Immutable point-in-time capture of watchlist results."""

    type: Literal["snapshot"] = "snapshot"  # pyright: ignore[reportIncompatibleVariableOverride]
    watchlist_id: Ulid
    source_ids: list[Ulid] = Field(default_factory=list)
    material_diff_from: Ulid | None = None  # previous snapshot for diff
    preserved_bytes_sha256: str = ""
    version: int = 1


class Observation(BaseRecord):
    """A material change detected between snapshots — review-only candidate."""

    type: Literal["observation"] = "observation"  # pyright: ignore[reportIncompatibleVariableOverride]
    watchlist_id: Ulid
    snapshot_id: Ulid
    previous_snapshot_id: Ulid | None = None
    change_summary: Annotated[str, Field(min_length=1, max_length=5000)]
    entity_ids: list[Ulid] = Field(default_factory=list)
    source_ids: list[Ulid] = Field(default_factory=list)
    version: int = 1


class Event(BaseRecord):
    """A time-anchored canonical event derived from observations."""

    type: Literal["event"] = "event"  # pyright: ignore[reportIncompatibleVariableOverride]
    entity_ids: list[Ulid] = Field(default_factory=list)
    event_date: Annotated[str, Field(min_length=1, max_length=100)] = ""
    event_type: Annotated[str, Field(min_length=1, max_length=100)] = "general"
    description: Annotated[str, Field(min_length=1, max_length=5000)]
    observation_ids: list[Ulid] = Field(default_factory=list)
    source_ids: list[Ulid] = Field(default_factory=list)
    version: int = 1


RECORD_MODELS = (BaseRecord, SourceItem, EntityRecord, RelationshipRecord, Claim, Interaction, Decision, Inquiry, SearchResult, Opportunity, CandidatePatch, ResearchRun, Analysis, Classification, Watchlist, Snapshot, Observation, Event)
