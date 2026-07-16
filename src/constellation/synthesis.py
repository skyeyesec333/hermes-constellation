"""Task-specific synthesis planning with local/LLM phase separation.

Does not call model providers. Records budgets, reuse, packet bounds, and
promotion eligibility for review-only synthesis workflows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .budgeting import TaskKind, build_budget_plan
from .models import ResearchTerminalState
from .research import ResearchBudget, ResearchLedger

_LOCAL_PHASES = ("preserve", "extract_or_transcribe", "map_or_index")
_LLM_PHASES = ("retrieve", "reason", "pressure_test", "synthesize", "review")


class SynthesisError(RuntimeError):
    """Raised when a synthesis plan or run violates its bounds."""


def _sha_ok(value: str | None) -> bool:
    return value is not None and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _retrieval_strategy(task_kind: TaskKind) -> dict[str, Any]:
    if task_kind == "book":
        return {
            "strategy": "hierarchical-map-first",
            "prefer": ["document-map", "chapter-summary", "segment"],
        }
    if task_kind == "deck":
        return {
            "strategy": "slide-map-first",
            "prefer": ["deck-map", "selected-slide", "segment"],
        }
    if task_kind == "meeting":
        return {
            "strategy": "meeting-map-first",
            "prefer": ["meeting-map", "transcript-segment", "notes-item"],
        }
    if task_kind == "paper":
        return {
            "strategy": "section-map-first",
            "prefer": ["document-map", "section", "segment"],
        }
    return {
        "strategy": "bounded-packet",
        "prefer": ["source-map", "segment"],
    }


def build_synthesis_plan(
    *,
    task_kind: TaskKind,
    source_bytes: int,
    estimated_pages: int | None = None,
    estimated_audio_minutes: float | None = None,
    derived_artifacts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic task plan. Local phases consume zero LLM tokens."""
    budget_plan = build_budget_plan(
        task_kind=task_kind,
        source_bytes=source_bytes,
        estimated_pages=estimated_pages,
        estimated_audio_minutes=estimated_audio_minutes,
    )
    profile = budget_plan.profile
    artifacts = list(derived_artifacts or [])
    for artifact in artifacts:
        digest = artifact.get("content_sha256")
        if not _sha_ok(digest):
            raise SynthesisError("derived artifact content_sha256 must be sha256 hex")
        if not str(artifact.get("kind") or "").strip():
            raise SynthesisError("derived artifact kind is required")

    reuse_count = len(artifacts)
    base_calls = profile.max_model_calls
    # Each reusable derived artifact can avoid one cold extraction/mapping LLM assist call.
    estimated_model_calls = max(1, base_calls - min(reuse_count, max(0, base_calls - 1)))

    contradiction_share = 0.35 if task_kind == "competitive_analysis" else 0.15
    retrieve_meta = _retrieval_strategy(task_kind)

    phases: list[dict[str, Any]] = [
        {
            "name": "preserve",
            "local": True,
            "llm_tokens": 0,
            "description": "Preserve original bytes and hashes",
        },
        {
            "name": "extract_or_transcribe",
            "local": True,
            "llm_tokens": 0,
            "description": "Local extraction/OCR/transcription only",
            **(
                {
                    "audio_minutes": estimated_audio_minutes,
                    "compute": "local-transcription",
                }
                if task_kind == "meeting" and estimated_audio_minutes is not None
                else {}
            ),
        },
        {
            "name": "map_or_index",
            "local": True,
            "llm_tokens": 0,
            "description": "Build local maps/indexes; no whole-document prompt",
        },
        {
            "name": "retrieve",
            "local": False,
            "llm_tokens": min(profile.max_input_tokens, profile.chunk_target_tokens * 4),
            "lane": "collection",
            **retrieve_meta,
        },
        {
            "name": "reason",
            "local": False,
            "llm_tokens": profile.max_output_tokens,
            "lane": "adjudication",
        },
        {
            "name": "pressure_test",
            "local": False,
            "llm_tokens": int(profile.max_output_tokens * contradiction_share)
            if task_kind == "competitive_analysis"
            else max(500, int(profile.max_output_tokens * contradiction_share)),
            "lane": "adjudication",
            "purpose": "contradiction-check",
        },
        {
            "name": "synthesize",
            "local": False,
            "llm_tokens": profile.max_output_tokens,
            "lane": "synthesis",
        },
        {
            "name": "review",
            "local": False,
            "llm_tokens": max(500, int(profile.max_output_tokens * 0.25)),
            "lane": "evaluation",
        },
    ]

    return {
        "version": 1,
        "task_kind": task_kind,
        "phases": phases,
        "budget": {
            "max_source_bytes": profile.max_source_bytes,
            "max_pages": profile.max_pages,
            "max_audio_minutes": profile.max_audio_minutes,
            "chunk_target_tokens": profile.chunk_target_tokens,
            "evidence_packet_bytes": profile.evidence_packet_bytes,
            "max_model_calls": profile.max_model_calls,
            "max_input_tokens": profile.max_input_tokens,
            "max_output_tokens": profile.max_output_tokens,
            "max_cost_usd": profile.max_cost_usd,
            "synthesis_reserve": profile.synthesis_reserve,
            "contradiction_share": contradiction_share,
        },
        "derived_artifacts": artifacts,
        "estimated_model_calls": estimated_model_calls,
        "requires_confirmation": budget_plan.requires_confirmation,
        "warnings": list(budget_plan.warnings),
        "promotion_allowed": False,
        "whole_document_prompt_allowed": False,
    }


def research_budget_for_task(task_kind: TaskKind, source_bytes: int = 0) -> ResearchBudget:
    """Map a task profile onto a ResearchBudget for LLM phases only."""
    plan = build_synthesis_plan(task_kind=task_kind, source_bytes=max(0, source_bytes))
    budget = plan["budget"]
    return ResearchBudget(
        max_calls=int(budget["max_model_calls"]),
        max_tokens=int(budget["max_input_tokens"] + budget["max_output_tokens"]),
        max_cost_usd=float(budget["max_cost_usd"]),
        max_context_bytes=int(budget["evidence_packet_bytes"] * 4),
        synthesis_reserve=float(budget["synthesis_reserve"]),
    )


@dataclass
class SynthesisRun:
    """In-memory synthesis execution state bound to a research ledger."""

    plan: dict[str, Any]
    ledger: ResearchLedger
    provider: str
    model: str
    completed_local: set[str] = field(default_factory=set)
    llm_events: list[dict[str, Any]] = field(default_factory=list)
    no_delta_streak: int = 0
    reused_artifacts: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    _terminal_receipt: dict[str, Any] | None = None

    def mark_local_phase(
        self,
        phase: str,
        *,
        reused: bool = False,
        artifact_sha256: str | None = None,
    ) -> None:
        if self._terminal_receipt is not None:
            raise SynthesisError("synthesis run is already terminal")
        if phase not in _LOCAL_PHASES:
            raise SynthesisError("unknown local phase")
        if reused:
            if not _sha_ok(artifact_sha256):
                raise SynthesisError("reused local phase requires artifact sha256")
            self.reused_artifacts.append(artifact_sha256)  # type: ignore[arg-type]
        self.completed_local.add(phase)

    def record_llm_phase(
        self,
        *,
        phase: str,
        lane: str,
        tokens: int,
        cost_usd: float,
        context_bytes: int,
        source_ids: list[str],
        packet_bytes: int,
        evidence_delta: bool = True,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        if self._terminal_receipt is not None:
            raise SynthesisError("synthesis run is already terminal")
        if phase not in _LLM_PHASES:
            raise SynthesisError("unknown LLM phase")
        if not self.completed_local.issuperset(_LOCAL_PHASES):
            raise SynthesisError("local phases must complete before LLM phases")
        limit = int(self.plan["budget"]["evidence_packet_bytes"])
        if packet_bytes < 0:
            raise SynthesisError("packet_bytes cannot be negative")
        if packet_bytes > limit:
            raise SynthesisError("evidence packet exceeds configured byte budget")
        if tokens > int(self.plan["budget"]["max_input_tokens"] + self.plan["budget"]["max_output_tokens"]):
            raise SynthesisError("phase tokens exceed configured token budget")

        self.ledger.record_call(
            lane=lane,
            provider=self.provider,
            model=self.model,
            success=True,
            tokens=tokens,
            cost_usd=cost_usd,
            context_bytes=context_bytes,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_tokens=tokens,
            estimated_cost_usd=cost_usd,
        )
        for source_id in source_ids:
            if source_id and source_id not in self.source_ids:
                self.source_ids.append(source_id)
        self.llm_events.append(
            {
                "phase": phase,
                "lane": lane,
                "tokens": tokens,
                "packet_bytes": packet_bytes,
                "source_ids": list(source_ids),
                "evidence_delta": evidence_delta,
            }
        )
        if evidence_delta:
            self.no_delta_streak = 0
        else:
            self.no_delta_streak += 1

    def should_stop_for_no_delta(self) -> bool:
        return self.no_delta_streak >= 2

    def _receipt_with_plan(self) -> dict[str, Any]:
        receipt = self.ledger.receipt()
        receipt["synthesis_plan"] = {
            "task_kind": self.plan["task_kind"],
            "estimated_model_calls": self.plan["estimated_model_calls"],
            "evidence_packet_bytes": self.plan["budget"]["evidence_packet_bytes"],
            "whole_document_prompt_allowed": False,
            "reused_artifacts": list(self.reused_artifacts),
            "source_ids": list(self.source_ids),
            "llm_events": list(self.llm_events),
            "completed_local_phases": sorted(self.completed_local),
        }
        return receipt

    def finish_exhausted(self) -> dict[str, Any]:
        if self.ledger.status is None:
            self.ledger.finish(
                ResearchTerminalState.BUDGET_EXHAUSTED,
                stop_reason="budget exhausted during synthesis",
                unresolved_gaps=["budget exhausted before synthesis completed"],
            )
        elif self.ledger.status is not ResearchTerminalState.BUDGET_EXHAUSTED:
            raise SynthesisError("ledger is terminal for a different reason")
        if not self.ledger.unresolved_gaps:
            self.ledger.unresolved_gaps = ["budget exhausted before synthesis completed"]
        self._terminal_receipt = self._receipt_with_plan()
        return self._terminal_receipt

    def finish_no_delta(self) -> dict[str, Any]:
        if self.ledger.status is not None:
            raise SynthesisError("synthesis run is already terminal")
        self.ledger.finish(
            ResearchTerminalState.PARTIAL,
            stop_reason="stopped after two bounded lanes with no adjudicated evidence delta",
            unresolved_gaps=["no adjudicated evidence delta after two retrieval lanes"],
        )
        self._terminal_receipt = self._receipt_with_plan()
        return self._terminal_receipt

    def finish_completed(
        self,
        *,
        pivotal_claims: list[str] | None = None,
        contradictions: list[str] | None = None,
        unresolved_gaps: list[str] | None = None,
    ) -> dict[str, Any]:
        if self.ledger.status is not None:
            raise SynthesisError("synthesis run is already terminal")
        if self.no_delta_streak >= 2:
            raise SynthesisError("cannot complete after two no-delta lanes")
        self.ledger.finish(
            ResearchTerminalState.COMPLETED,
            stop_reason="synthesis plan completed within budget",
            pivotal_claims=pivotal_claims,
            contradictions=contradictions,
            unresolved_gaps=unresolved_gaps,
        )
        self._terminal_receipt = self._receipt_with_plan()
        return self._terminal_receipt


def create_synthesis_run(
    *,
    task_kind: TaskKind,
    source_bytes: int,
    provider: str,
    model: str,
    estimated_pages: int | None = None,
    estimated_audio_minutes: float | None = None,
    derived_artifacts: list[dict[str, str]] | None = None,
) -> SynthesisRun:
    """Create a synthesis run with task plan + research ledger."""
    if not provider.strip() or not model.strip():
        raise SynthesisError("provider and model are required")
    plan = build_synthesis_plan(
        task_kind=task_kind,
        source_bytes=source_bytes,
        estimated_pages=estimated_pages,
        estimated_audio_minutes=estimated_audio_minutes,
        derived_artifacts=derived_artifacts,
    )
    ledger = ResearchLedger(
        research_budget_for_task(task_kind, source_bytes=source_bytes),
        workflow=f"synthesis:{task_kind}",
        workflow_version="1",
        prompt_version="synthesis-planner-v1",
    )
    return SynthesisRun(plan=plan, ledger=ledger, provider=provider, model=model)
