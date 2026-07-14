"""Deterministic task-aware preflight budgets; no provider calls occur here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TaskKind = Literal[
    "business_card",
    "meeting",
    "deck",
    "paper",
    "book",
    "email_refresh",
    "competitive_analysis",
]


@dataclass(frozen=True, slots=True)
class TaskBudgetProfile:
    task_kind: TaskKind
    max_source_bytes: int
    max_pages: int | None
    max_audio_minutes: int | None
    chunk_target_tokens: int
    evidence_packet_bytes: int
    max_model_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_usd: float
    synthesis_reserve: float


@dataclass(frozen=True, slots=True)
class BudgetPlan:
    task_kind: TaskKind
    profile: TaskBudgetProfile
    source_bytes: int
    estimated_pages: int | None
    estimated_audio_minutes: float | None
    phases: tuple[dict[str, int | str], ...]
    requires_confirmation: bool
    warnings: tuple[str, ...]

    @property
    def synthesis_reserve(self) -> float:
        return self.profile.synthesis_reserve


_PROFILES: dict[TaskKind, TaskBudgetProfile] = {
    "business_card": TaskBudgetProfile("business_card", 16 * 1024 * 1024, 2, None, 1_500, 16_384, 2, 12_000, 3_000, 1.0, 0.25),
    "meeting": TaskBudgetProfile("meeting", 50 * 1024 * 1024, None, 180, 2_000, 49_152, 4, 48_000, 6_000, 4.0, 0.30),
    "deck": TaskBudgetProfile("deck", 50 * 1024 * 1024, 500, None, 2_000, 65_536, 6, 64_000, 8_000, 6.0, 0.30),
    "paper": TaskBudgetProfile("paper", 50 * 1024 * 1024, 500, None, 2_000, 65_536, 6, 80_000, 8_000, 6.0, 0.30),
    "book": TaskBudgetProfile("book", 50 * 1024 * 1024, 500, None, 2_000, 98_304, 12, 96_000, 12_000, 12.0, 0.35),
    "email_refresh": TaskBudgetProfile("email_refresh", 16 * 1024 * 1024, None, None, 1_500, 32_768, 3, 24_000, 4_000, 2.0, 0.30),
    "competitive_analysis": TaskBudgetProfile("competitive_analysis", 50 * 1024 * 1024, None, None, 2_000, 131_072, 12, 160_000, 12_000, 16.0, 0.35),
}


def build_budget_plan(
    *,
    task_kind: TaskKind,
    source_bytes: int,
    estimated_pages: int | None = None,
    estimated_audio_minutes: float | None = None,
) -> BudgetPlan:
    """Return a deterministic preflight plan without reading source content or calling a provider."""
    if source_bytes < 0:
        raise ValueError("source_bytes cannot be negative")
    if estimated_pages is not None and estimated_pages < 0:
        raise ValueError("estimated_pages cannot be negative")
    if estimated_audio_minutes is not None and estimated_audio_minutes < 0:
        raise ValueError("estimated_audio_minutes cannot be negative")
    try:
        profile = _PROFILES[task_kind]
    except KeyError as exc:
        raise ValueError("unknown task kind") from exc

    warnings: list[str] = []
    if source_bytes > profile.max_source_bytes:
        warnings.append("source exceeds default profile size")
    if profile.max_pages is not None and estimated_pages is not None and estimated_pages > profile.max_pages:
        warnings.append("page count exceeds default profile limit")
    if (
        profile.max_audio_minutes is not None
        and estimated_audio_minutes is not None
        and estimated_audio_minutes > profile.max_audio_minutes
    ):
        warnings.append("audio duration exceeds default profile limit")
    if task_kind == "book" and warnings:
        warnings.append("explicit long-form mode")

    return BudgetPlan(
        task_kind=task_kind,
        profile=profile,
        source_bytes=source_bytes,
        estimated_pages=estimated_pages,
        estimated_audio_minutes=estimated_audio_minutes,
        phases=(
            {"name": "extract_and_map", "model_tokens": 0},
            {"name": "retrieve", "model_tokens": profile.max_input_tokens},
            {"name": "synthesize", "model_tokens": profile.max_output_tokens},
        ),
        requires_confirmation=bool(warnings),
        warnings=tuple(warnings),
    )
