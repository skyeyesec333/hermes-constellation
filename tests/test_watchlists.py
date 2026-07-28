"""Tests for watchlists and temporal intelligence."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import constellation.watchlists as watchlists
from constellation.cli import build_parser, run_action
from constellation.frontmatter import render_frontmatter
from constellation.models import Sensitivity, SourceItem, Watchlist, generate_ulid
from constellation.watchlists import (
    stage_event,
    stage_observation,
    stage_snapshot,
    stage_watchlist,
)
from constellation.vault import initialize_vault


NOW = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)


def _setup_execution_vault(tmp_path: Path) -> tuple[Path, str, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    source_id = generate_ulid()
    source = SourceItem(
        id=source_id,
        type="source_item",
        title="Watch source",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"source bytes").hexdigest(),
        original_path="Library/Files/watch-source.txt",
        media_type="text/plain",
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "source-items" / f"{source_id}.md").write_text(
        render_frontmatter(source.model_dump(mode="json", exclude_none=True), "# Source\n"),
        encoding="utf-8",
    )

    watchlist_id = generate_ulid()
    watchlist = Watchlist(
        id=watchlist_id,
        title="Competitor watch",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        query_terms=["acme"],
        sources=["local"],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "watchlists" / f"{watchlist_id}.md").write_text(
        render_frontmatter(watchlist.model_dump(mode="json", exclude_none=True), "# Watchlist\n"),
        encoding="utf-8",
    )
    return vault, watchlist_id, source_id


def _add_canonical_source(vault: Path) -> str:
    source_id = generate_ulid()
    source = SourceItem(
        id=source_id,
        type="source_item",
        title="Extra watch source",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"extra source bytes").hexdigest(),
        original_path="Library/Files/extra-watch-source.txt",
        media_type="text/plain",
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "source-items" / f"{source_id}.md").write_text(
        render_frontmatter(source.model_dump(mode="json", exclude_none=True), "# Source\n"),
        encoding="utf-8",
    )
    return source_id


def _add_second_watchlist(vault: Path) -> str:
    watchlist_id = generate_ulid()
    watchlist = Watchlist(
        id=watchlist_id,
        title="Second watchlist",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        query_terms=["other"],
        sources=["local"],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "watchlists" / f"{watchlist_id}.md").write_text(
        render_frontmatter(watchlist.model_dump(mode="json", exclude_none=True), "# Watchlist\n"),
        encoding="utf-8",
    )
    return watchlist_id


def _change_key(watchlist_id: str, previous_hash: str, current_hash: str) -> str:
    return hashlib.sha256(
        f"{watchlist_id}\0{previous_hash}\0{current_hash}".encode()
    ).hexdigest()


def _normalized_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def test_execute_watchlist_snapshot_stages_source_grounded_snapshot_and_receipt(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)

    result = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="  Alpha  \r\nBeta\t\r\n",
    )

    assert result["status"] == "snapshot_staged"
    assert result["material_change"] is False
    assert result["observation_candidate_path"] is None
    assert (vault / result["snapshot_candidate_path"]).is_file()
    receipt_path = vault / result["receipt_path"]
    assert receipt_path.is_file()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected_hash = hashlib.sha256(b"Alpha\nBeta\n").hexdigest()
    assert receipt["normalized_content_sha256"] == expected_hash
    assert receipt["source_ids"] == [source_id]


def test_execute_watchlist_snapshot_stages_observation_for_material_change(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)
    first = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="Alpha\n",
    )

    second = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="Alpha\nBeta\n",
        previous_snapshot_id=str(first["snapshot_id"]),
    )

    assert second["status"] == "material_change_staged"
    assert second["material_change"] is True
    observation_path = vault / str(second["observation_candidate_path"])
    assert observation_path.is_file()
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    assert observation["previous_snapshot_id"] == first["snapshot_id"]
    assert observation["snapshot_id"] == second["snapshot_id"]
    assert observation["source_ids"] == [source_id]


def test_execute_watchlist_snapshot_deduplicates_same_material_change(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)
    first = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="Alpha\n",
    )
    second = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="Beta\n",
        previous_snapshot_id=str(first["snapshot_id"]),
    )

    repeated = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="Beta\n",
        previous_snapshot_id=str(first["snapshot_id"]),
    )

    assert repeated["status"] == "duplicate_change"
    assert repeated["observation_candidate_path"] == second["observation_candidate_path"]
    observation_candidates = list((vault / ".constellation/candidates").glob("observation-*.json"))
    assert len(observation_candidates) == 1


def test_execute_watchlist_snapshot_ignores_formatting_only_change(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)
    first = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="Alpha\r\nBeta\r\n",
    )

    second = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="  Alpha  \nBeta\t\n",
        previous_snapshot_id=str(first["snapshot_id"]),
    )

    assert second["status"] == "snapshot_staged"
    assert second["material_change"] is False
    assert second["observation_candidate_path"] is None
    assert list((vault / ".constellation/candidates").glob("observation-*.json")) == []


def test_watch_run_cli_dispatches_execution_path(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)
    values = vars(
        build_parser().parse_args(
            [
                "watch-run",
                str(vault),
                "--watchlist-id",
                watchlist_id,
                "--source-ids",
                source_id,
                "--content",
                "Alpha",
            ]
        )
    )

    result = run_action(str(values.pop("command")), values)

    assert result["status"] == "snapshot_staged"
    assert (vault / result["receipt_path"]).is_file()


# ── Step 2 safety gaps ───────────────────────────────────────────────────


def test_execute_watchlist_snapshot_fails_closed_for_missing_watchlist(tmp_path: Path) -> None:
    vault, _, source_id = _setup_execution_vault(tmp_path)
    missing_id = generate_ulid()

    try:
        watchlists.execute_watchlist_snapshot(
            vault,
            watchlist_id=missing_id,
            source_ids=[source_id],
            preserved_content="Alpha\n",
        )
        raise AssertionError("expected WatchlistError")
    except watchlists.WatchlistError as exc:
        assert "canonical watchlist not found" in str(exc)


def test_execute_watchlist_snapshot_fails_closed_for_empty_source_list(tmp_path: Path) -> None:
    vault, watchlist_id, _ = _setup_execution_vault(tmp_path)

    try:
        watchlists.execute_watchlist_snapshot(
            vault,
            watchlist_id=watchlist_id,
            source_ids=[],
            preserved_content="Alpha\n",
        )
        raise AssertionError("expected WatchlistError")
    except watchlists.WatchlistError as exc:
        assert "at least one canonical source" in str(exc)


def test_execute_watchlist_snapshot_fails_closed_for_missing_source(tmp_path: Path) -> None:
    vault, watchlist_id, _ = _setup_execution_vault(tmp_path)
    missing_source = generate_ulid()

    try:
        watchlists.execute_watchlist_snapshot(
            vault,
            watchlist_id=watchlist_id,
            source_ids=[missing_source],
            preserved_content="Alpha\n",
        )
        raise AssertionError("expected WatchlistError")
    except watchlists.WatchlistError as exc:
        assert "canonical source item not found" in str(exc)


def test_execute_watchlist_snapshot_fails_closed_for_empty_normalized_content(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)

    try:
        watchlists.execute_watchlist_snapshot(
            vault,
            watchlist_id=watchlist_id,
            source_ids=[source_id],
            preserved_content="   \n\t\n  \r\n",
        )
        raise AssertionError("expected WatchlistError")
    except watchlists.WatchlistError as exc:
        assert "empty after normalization" in str(exc)


def test_execute_watchlist_snapshot_fails_closed_for_cross_watchlist_receipt(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)
    other_watchlist = _add_second_watchlist(vault)
    other_run = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=other_watchlist,
        source_ids=[source_id],
        preserved_content="Alpha\n",
    )

    try:
        watchlists.execute_watchlist_snapshot(
            vault,
            watchlist_id=watchlist_id,
            source_ids=[source_id],
            preserved_content="Beta\n",
            previous_snapshot_id=str(other_run["snapshot_id"]),
        )
        raise AssertionError("expected WatchlistError")
    except watchlists.WatchlistError as exc:
        assert "different watchlist" in str(exc)


def test_execute_watchlist_snapshot_fails_closed_for_malformed_marker(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)
    first = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="Alpha\n",
    )
    key = _change_key(watchlist_id, _normalized_hash("Alpha\n"), _normalized_hash("Beta\n"))
    marker_dir = vault / ".constellation/watchlist-runs/material-changes"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / f"{key}.json").write_text("not-json{", encoding="utf-8")

    try:
        watchlists.execute_watchlist_snapshot(
            vault,
            watchlist_id=watchlist_id,
            source_ids=[source_id],
            preserved_content="Beta\n",
            previous_snapshot_id=str(first["snapshot_id"]),
        )
        raise AssertionError("expected WatchlistError")
    except watchlists.WatchlistError as exc:
        assert "marker is invalid" in str(exc)


def test_execute_watchlist_snapshot_pending_marker_raises_recoverable_error(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)
    first = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="Alpha\n",
    )
    key = _change_key(watchlist_id, _normalized_hash("Alpha\n"), _normalized_hash("Beta\n"))
    marker_dir = vault / ".constellation/watchlist-runs/material-changes"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "status": "pending",
                "change_key": key,
                "watchlist_id": watchlist_id,
                "previous_content_sha256": _normalized_hash("Alpha\n"),
                "current_content_sha256": _normalized_hash("Beta\n"),
                "observation_candidate_path": None,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        watchlists.execute_watchlist_snapshot(
            vault,
            watchlist_id=watchlist_id,
            source_ids=[source_id],
            preserved_content="Beta\n",
            previous_snapshot_id=str(first["snapshot_id"]),
        )
        raise AssertionError("expected WatchlistError")
    except watchlists.WatchlistError as exc:
        assert "incomplete" in str(exc)
        assert key in str(exc)


def test_execute_watchlist_snapshot_receipt_sorts_source_ids(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)
    second_source = _add_canonical_source(vault)
    ordered = sorted([source_id, second_source])
    reversed_order = list(reversed(ordered))

    result = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=reversed_order,
        preserved_content="Alpha\n",
    )

    receipt = json.loads((vault / str(result["receipt_path"])).read_text(encoding="utf-8"))
    assert receipt["source_ids"] == ordered


def test_execute_watchlist_snapshot_receipt_contains_no_raw_content(tmp_path: Path) -> None:
    vault, watchlist_id, source_id = _setup_execution_vault(tmp_path)

    result = watchlists.execute_watchlist_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[source_id],
        preserved_content="Alpha SECRET-TOKEN-XYZ\n",
    )

    receipt_text = (vault / str(result["receipt_path"])).read_text(encoding="utf-8")
    assert "SECRET-TOKEN-XYZ" not in receipt_text


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
