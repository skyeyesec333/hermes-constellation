"""Bounded, deterministic evidence packets for review-only intelligence work."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Ulid
from .retrieval import search
from .storage import atomic_write_text, safe_relative_path
from .vault import is_initialized


class IntelligenceError(RuntimeError):
    """Raised when an evidence-backed candidate cannot be safely staged."""


class StrategicOption(BaseModel):
    """One actionable option with an explicit reversible test and stop rule."""

    model_config = ConfigDict(extra="forbid", strict=True)

    move: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=4000)
    enabling_actors: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(min_length=1)
    expected_upside: str = Field(min_length=1, max_length=2000)
    failure_modes: list[str] = Field(min_length=1)
    first_reversible_test: str = Field(min_length=1, max_length=2000)
    kill_criteria: list[str] = Field(min_length=1)

    @field_validator("move", "rationale", "expected_upside", "first_reversible_test")
    @classmethod
    def option_text_is_nonblank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("strategy option text cannot be blank")
        return cleaned

    @field_validator("enabling_actors", "constraints", "failure_modes", "kill_criteria")
    @classmethod
    def option_lists_are_nonblank(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("strategy option list values cannot be blank")
        return cleaned


class NoveltyAssessment(BaseModel):
    """Deterministic gate before any future canonical note creation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    classification: Literal[
        "novel",
        "update_existing",
        "insufficient_novelty",
        "contradiction_only",
        "needs_identity_resolution",
    ]
    new_facts: list[str]
    existing_note_ids: list[Ulid]
    source_note_ids: list[Ulid] = Field(min_length=1)
    why_it_matters: str = Field(min_length=1, max_length=2000)
    uncertainties: list[str] = Field(min_length=1)

    @field_validator("new_facts", "uncertainties")
    @classmethod
    def facts_are_nonblank(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("novelty values cannot be blank")
        return cleaned

    @field_validator("why_it_matters")
    @classmethod
    def why_is_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("novelty rationale cannot be blank")
        return value


def classify_novelty(
    *,
    new_facts: list[str],
    existing_note_ids: list[Ulid],
    source_note_ids: list[Ulid],
    identity_resolved: bool,
    why_it_matters: str,
    uncertainties: list[str],
    contradictions: bool = False,
) -> NoveltyAssessment:
    """Classify a proposed delta without creating or modifying canonical notes."""
    if not identity_resolved:
        classification = "needs_identity_resolution"
    elif not new_facts and contradictions:
        classification = "contradiction_only"
    elif not new_facts:
        classification = "insufficient_novelty"
    elif existing_note_ids:
        classification = "update_existing"
    else:
        classification = "novel"
    return NoveltyAssessment(
        classification=classification,
        new_facts=new_facts,
        existing_note_ids=existing_note_ids,
        source_note_ids=source_note_ids,
        why_it_matters=why_it_matters,
        uncertainties=uncertainties,
    )


_PROHIBITED_HYPOTHESIS_CONTENT = re.compile(
    r"\b(?:mental[ -]?health|diagnos\w*|personality[ -]?disorder|protected[ -]?trait|"
    r"vulnerabilit\w*|manipulat\w*|narciss\w*)\b",
    re.IGNORECASE,
)


class ProfessionalHypothesis(BaseModel):
    """Evidence-backed, non-diagnostic observation for professional decision support."""

    model_config = ConfigDict(extra="forbid", strict=True)

    dimension: Literal[
        "communication",
        "decision_tempo",
        "public_mandate",
        "incentives",
        "collaboration_posture",
    ]
    observation: str = Field(min_length=1, max_length=2000)
    working_hypothesis: str = Field(min_length=1, max_length=2000)
    source_note_ids: list[Ulid] = Field(min_length=1)
    is_working_hypothesis: Literal[True] = True

    @field_validator("observation", "working_hypothesis")
    @classmethod
    def content_is_professional_and_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("professional hypothesis content cannot be blank")
        if _PROHIBITED_HYPOTHESIS_CONTENT.search(value):
            raise ValueError("professional hypothesis content is not allowed")
        return value


class StrategyCandidate(BaseModel):
    """A review-only strategic option grounded in one immutable evidence packet."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    candidate_id: Ulid
    status: Literal["draft"] = "draft"
    question: str = Field(min_length=1, max_length=500)
    option: StrategicOption
    evidence_packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_note_ids: list[Ulid] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    assumptions: list[str] = Field(min_length=1)
    uncertainties: list[str] = Field(min_length=1)
    falsifiers: list[str] = Field(min_length=1)
    next_tests: list[str] = Field(min_length=1)
    professional_hypotheses: list[ProfessionalHypothesis] = Field(default_factory=list)
    human_review_required: Literal[True] = True

    @field_validator("assumptions", "uncertainties", "falsifiers", "next_tests")
    @classmethod
    def review_items_are_nonblank(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("strategy review items cannot be blank")
        return cleaned


def build_evidence_packet(
    root: Path | str,
    query: str,
    *,
    limit: int = 10,
    max_bytes: int = 32_768,
    sensitivity_ceiling: str = "restricted",
) -> dict[str, object]:
    """Retrieve a small anchored packet; never reads or writes beyond canonical search."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    retrieved = search(root, query, limit=limit, sensitivity_ceiling=sensitivity_ceiling)
    if retrieved["status"] != "evidence_found":
        return {
            "status": "evidence_not_ready",
            "query": query,
            "evidence": [],
            "reason": retrieved.get("reason", retrieved["status"]),
        }
    evidence_raw = retrieved["evidence"]
    if not isinstance(evidence_raw, list):
        raise ValueError("retrieval evidence has an invalid shape")
    evidence = evidence_raw
    packet = {
        "status": "evidence_ready",
        "query": query,
        "sensitivity_ceiling": sensitivity_ceiling,
        "evidence": evidence,
    }
    encoded = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("evidence packet exceeds max_bytes")
    return {**packet, "bytes": len(encoded), "packet_sha256": hashlib.sha256(encoded).hexdigest()}


def _verified_packet_note_ids(packet: dict[str, object]) -> tuple[str, set[str]]:
    if packet.get("status") != "evidence_ready":
        raise IntelligenceError("strategy candidate requires an evidence-ready packet")
    core = {
        "status": packet.get("status"),
        "query": packet.get("query"),
        "sensitivity_ceiling": packet.get("sensitivity_ceiling"),
        "evidence": packet.get("evidence"),
    }
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if packet.get("packet_sha256") != digest or packet.get("bytes") != len(encoded):
        raise IntelligenceError("evidence packet hash or byte count is invalid")
    evidence = packet.get("evidence")
    if not isinstance(evidence, list):
        raise IntelligenceError("evidence packet has an invalid evidence shape")
    note_ids = {
        str(item["note_id"])
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("note_id"), str)
    }
    if len(note_ids) != len(evidence):
        raise IntelligenceError("every evidence item requires a note_id")
    return digest, note_ids


def stage_strategy_candidate(
    root: Path | str,
    packet: dict[str, object],
    input_path: Path | str,
) -> dict[str, str]:
    """Validate and atomically stage a draft; never write canonical records."""
    if not is_initialized(root):
        raise IntelligenceError("strategy staging requires an initialized vault")
    digest, packet_note_ids = _verified_packet_note_ids(packet)
    source = Path(input_path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise IntelligenceError("strategy candidate input must be a regular file")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise IntelligenceError("strategy candidate YAML is unreadable") from exc
    if not isinstance(raw, dict):
        raise IntelligenceError("strategy candidate YAML must contain a mapping")
    candidate = StrategyCandidate.model_validate(raw)
    if candidate.evidence_packet_sha256 != digest:
        raise IntelligenceError("strategy candidate references a different evidence packet")
    if not set(candidate.evidence_note_ids).issubset(packet_note_ids):
        raise IntelligenceError("strategy candidate references evidence outside its packet")
    relative = Path(".constellation/candidates") / f"strategy-{candidate.candidate_id}.yaml"
    target = safe_relative_path(root, relative)
    if target.exists() or target.is_symlink():
        raise IntelligenceError("strategy candidate already exists")
    packet_relative = Path(".constellation/candidates") / f"evidence-{digest}.json"
    packet_target = safe_relative_path(root, packet_relative)
    packet_text = json.dumps(packet, indent=2, sort_keys=True) + "\n"
    if packet_target.exists() or packet_target.is_symlink():
        if packet_target.is_symlink() or not packet_target.is_file():
            raise IntelligenceError("stored evidence packet is unsafe")
        if packet_target.read_text(encoding="utf-8") != packet_text:
            raise IntelligenceError("stored evidence packet does not match its hash")
    else:
        atomic_write_text(root, packet_relative, packet_text)
    text = yaml.safe_dump(
        candidate.model_dump(mode="json"), allow_unicode=True, default_flow_style=False, sort_keys=True
    )
    atomic_write_text(root, relative, text)
    return {
        "status": candidate.status,
        "candidate_id": candidate.candidate_id,
        "evidence_packet_sha256": digest,
        "path": relative.as_posix(),
        "packet_path": packet_relative.as_posix(),
    }


def plan_task_synthesis(
    *,
    task_kind: str,
    source_bytes: int,
    estimated_pages: int | None = None,
    estimated_audio_minutes: float | None = None,
    derived_artifacts: list[dict[str, str]] | None = None,
) -> dict:
    """Return a review-only synthesis plan for intelligence work; no provider calls."""
    from .synthesis import build_synthesis_plan

    return build_synthesis_plan(
        task_kind=task_kind,  # type: ignore[arg-type]
        source_bytes=source_bytes,
        estimated_pages=estimated_pages,
        estimated_audio_minutes=estimated_audio_minutes,
        derived_artifacts=derived_artifacts,
    )
