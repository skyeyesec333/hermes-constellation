"""Bounded research profiles — operator-selected escalation ceilings.

Profiles: ``off`` (no research), ``low`` (default, targeted), ``standard``,
``deep`` (explicit escalation). Every profile declares its ceilings up front;
``off`` fails before any network, budget, or adapter work.
"""

from __future__ import annotations

from .research import ResearchBudget

PROFILE_NAMES = ("off", "low", "standard", "deep")

_PROFILE_BUDGETS = {
    "low": ResearchBudget(max_calls=2, max_tokens=20_000, max_cost_usd=2.0, max_context_bytes=200_000),
    "standard": ResearchBudget(max_calls=8, max_tokens=80_000, max_cost_usd=8.0, max_context_bytes=800_000),
    "deep": ResearchBudget(max_calls=16, max_tokens=200_000, max_cost_usd=20.0, max_context_bytes=2_000_000),
}


class ResearchProfileError(RuntimeError):
    """Raised when a research profile is unknown or forbids research."""


def resolve_profile(name: str) -> ResearchBudget:
    """Resolve an operator-selected profile to its declared budget.

    Fails closed on unknown profiles. ``off`` always raises: selecting it
    means no discovery, no model, no network — read existing evidence only.
    """
    normalized = str(name).strip().lower()
    if normalized == "off":
        raise ResearchProfileError(
            "research profile 'off': read existing evidence only — "
            "no discovery, model, or network work is permitted"
        )
    budget = _PROFILE_BUDGETS.get(normalized)
    if budget is None:
        raise ResearchProfileError(
            f"unknown research profile: {name!r} (expected one of {', '.join(PROFILE_NAMES)})"
        )
    return budget
