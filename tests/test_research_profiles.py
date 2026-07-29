"""Tests for bounded research profiles."""

from pathlib import Path

import pytest

from constellation.research_profiles import (
    ResearchProfileError,
    resolve_profile,
)


def test_profiles_declare_escalating_ceilings() -> None:
    low = resolve_profile("low")
    standard = resolve_profile("standard")
    deep = resolve_profile("deep")

    assert low.max_calls < standard.max_calls < deep.max_calls
    assert low.max_tokens < standard.max_tokens < deep.max_tokens
    assert low.max_cost_usd < standard.max_cost_usd < deep.max_cost_usd
    assert low.max_context_bytes < standard.max_context_bytes < deep.max_context_bytes


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(ResearchProfileError, match="unknown research profile"):
        resolve_profile("yolo")


def test_off_profile_forbids_research() -> None:
    with pytest.raises(ResearchProfileError, match="off"):
        resolve_profile("off")


def test_run_inquiry_off_profile_never_touches_network(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from constellation.models import Inquiry, Sensitivity, generate_ulid
    from constellation.research_runner import run_inquiry
    from constellation.vault import initialize_vault

    vault = tmp_path / "vault"
    initialize_vault(vault)
    inquiry = Inquiry(
        id=generate_ulid(), title="q", status="active",
        sensitivity=Sensitivity.INTERNAL, question="What does TestCo do?",
        max_unique_sources=3,
        created_at=datetime(2026, 7, 28, tzinfo=UTC),
        updated_at=datetime(2026, 7, 28, tzinfo=UTC),
    )

    with pytest.raises(ResearchProfileError):
        run_inquiry(vault, inquiry, profile="off")

    receipts = list((vault / ".constellation").rglob("*receipt*")) if (vault / ".constellation").exists() else []
    assert receipts == []
