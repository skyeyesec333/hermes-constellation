"""Tests for watchlists and temporal intelligence."""

from pathlib import Path

from constellation.watchlists import (
    stage_event,
    stage_observation,
    stage_snapshot,
    stage_watchlist,
)
from constellation.vault import initialize_vault


def test_stage_watchlist(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    result = stage_watchlist(vault, title="Competitor Watch", query_terms=["acme", "competitor"], sources=["gdelt"])
    assert result["status"] == "staged"
    assert (vault / result["candidate_path"]).is_file()


def test_stage_snapshot(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    r = stage_watchlist(vault, title="Test")
    wl_id = r["watchlist_id"]
    result = stage_snapshot(vault, watchlist_id=wl_id, preserved_content="test data")
    assert result["status"] == "staged"


def test_stage_observation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    r = stage_watchlist(vault, title="Test")
    snap = stage_snapshot(vault, watchlist_id=r["watchlist_id"])
    result = stage_observation(
        vault, watchlist_id=r["watchlist_id"],
        snapshot_id=snap["snapshot_id"],
        change_summary="Acme launched new product",
    )
    assert result["status"] == "staged"


def test_stage_event(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    result = stage_event(
        vault, title="Product Launch", description="Acme launched Widget v2",
        event_date="2026-07-15", event_type="product_launch",
    )
    assert result["status"] == "staged"
