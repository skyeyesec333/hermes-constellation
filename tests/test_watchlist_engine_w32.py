"""W3.2 watchlist execution engine — RED tests.

Covers the gaps the reconciled roadmap calls out beyond the Step-11
constructors: automatic previous-snapshot resolution, failed/degraded/stale
states, observation source citations, egress-gated HTTP collection from the
CLI, and the reviewed Observation→Event transition.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

import constellation.watchlists as watchlists
from constellation.cli import build_parser, run_action
from constellation.frontmatter import render_frontmatter
from constellation.models import Sensitivity, Watchlist, generate_ulid
from constellation.review import promote_candidate
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)


def _setup_vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    watchlist_id = generate_ulid()
    watchlist = Watchlist(
        id=watchlist_id,
        title="Engine watch",
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
    return vault, watchlist_id


def _fixture_dir(tmp_path: Path) -> Path:
    fixture = tmp_path / "fixture-feed"
    fixture.mkdir(exist_ok=True)
    (fixture / "item-1.txt").write_text("Acme launched a new product line\n", encoding="utf-8")
    (fixture / "item-2.txt").write_text("Acme hired a new CTO\n", encoding="utf-8")
    return fixture


def _receipts(vault: Path, watchlist_id: str) -> list[dict]:
    runs = vault / ".constellation/watchlist-runs"
    out = []
    for path in runs.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("watchlist_id") == watchlist_id:
            out.append(data)
    return out


# ── 1. Automatic previous-snapshot resolution ────────────────────────────


def test_run_watchlist_auto_resolves_previous_snapshot(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    fixture = _fixture_dir(tmp_path)
    caps = watchlists.RunCaps(max_items=10, max_bytes=1_000_000)

    first = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )
    assert first["material_change"] is False

    (fixture / "item-3.txt").write_text("Acme acquired a competitor\n", encoding="utf-8")
    second = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )

    assert second["material_change"] is True
    assert second["observation_candidate_path"] is not None
    receipt = json.loads((vault / str(second["receipt_path"])).read_text(encoding="utf-8"))
    assert receipt["previous_snapshot_id"] == first["snapshot_id"]

    # unchanged rerun: resolves second run, same hash — no duplicate observation
    third = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )
    assert third["material_change"] is False
    observations = list((vault / ".constellation/candidates").glob("observation-*.json"))
    assert len(observations) == 1


def test_run_watchlist_explicit_previous_id_still_fail_closed(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    fixture = _fixture_dir(tmp_path)

    with pytest.raises(watchlists.WatchlistError, match="previous snapshot receipt not found"):
        watchlists.run_watchlist(
            vault, watchlist_id=watchlist_id,
            connector=watchlists.LocalFixtureConnector(fixture),
            previous_snapshot_id=generate_ulid(),
        )


# ── 2. Failed state writes a truthful receipt ─────────────────────────────


class _FailingConnector:
    def name(self) -> str:
        return "failing-fixture"

    def fetch(self, caps):  # noqa: ANN001
        raise RuntimeError("boom SECRET-TOKEN-XYZ")


def test_run_watchlist_failed_connector_writes_failed_receipt(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)

    with pytest.raises(watchlists.WatchlistError, match="connector fetch failed"):
        watchlists.run_watchlist(
            vault, watchlist_id=watchlist_id, connector=_FailingConnector(),
        )

    receipts = _receipts(vault, watchlist_id)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["status"] == "failed"
    assert receipt["connector"] == "failing-fixture"
    assert receipt["error"] == "RuntimeError"
    receipt_text = json.dumps(receipt)
    assert "SECRET-TOKEN-XYZ" not in receipt_text


# ── 3. Degraded auto-resolution skips corrupt receipts ────────────────────


def test_run_watchlist_skips_corrupt_receipt_flagged_degraded(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    fixture = _fixture_dir(tmp_path)
    caps = watchlists.RunCaps(max_items=10, max_bytes=1_000_000)

    first = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )

    # corrupt receipt that sorts NEWER than the valid one
    corrupt = {
        "schema_version": "0.1",
        "status": "ok",
        "watchlist_id": watchlist_id,
        "snapshot_id": generate_ulid(),
        "normalized_content_sha256": "0" * 64,
        "recorded_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    corrupt_name = f"zz{generate_ulid()}.json"
    (vault / ".constellation/watchlist-runs" / corrupt_name).write_text(
        json.dumps(corrupt)[:20] + "{not-json", encoding="utf-8"
    )

    (fixture / "item-3.txt").write_text("Acme acquired a competitor\n", encoding="utf-8")
    second = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )

    receipt = json.loads((vault / str(second["receipt_path"])).read_text(encoding="utf-8"))
    assert receipt["degraded"] is True
    assert corrupt_name in receipt["skipped_receipts"]
    assert receipt["previous_snapshot_id"] == first["snapshot_id"]
    assert second["material_change"] is True


# ── 4. watch-status: fresh / stale / degraded / no_runs ───────────────────


def test_watch_status_reports_states(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)

    status = watchlists.watch_status(vault, stale_after_hours=24)
    entry = [s for s in status["watchlists"] if s["watchlist_id"] == watchlist_id][0]
    assert entry["state"] == "no_runs"

    fixture = _fixture_dir(tmp_path)
    watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture),
    )

    fresh = watchlists.watch_status(vault, stale_after_hours=24)
    entry = [s for s in fresh["watchlists"] if s["watchlist_id"] == watchlist_id][0]
    assert entry["state"] == "fresh"
    assert entry["last_snapshot_id"] is not None
    assert entry["last_recorded_at"] is not None

    stale = watchlists.watch_status(vault, stale_after_hours=0)
    entry = [s for s in stale["watchlists"] if s["watchlist_id"] == watchlist_id][0]
    assert entry["state"] == "stale"

    (vault / ".constellation/watchlist-runs" / f"broken{generate_ulid()}.json").write_text(
        "{oops", encoding="utf-8"
    )
    degraded = watchlists.watch_status(vault, stale_after_hours=24)
    entry = [s for s in degraded["watchlists"] if s["watchlist_id"] == watchlist_id][0]
    assert entry["state"] == "degraded"


def test_watch_status_cli(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    fixture = _fixture_dir(tmp_path)
    watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture),
    )

    values = vars(build_parser().parse_args(["watch-status", str(vault)]))
    result = run_action(str(values.pop("command")), values)
    assert any(s["watchlist_id"] == watchlist_id for s in result["watchlists"])


# ── 5. Observation carries source citations ───────────────────────────────


def test_run_watchlist_observation_carries_source_citations(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    fixture = _fixture_dir(tmp_path)
    caps = watchlists.RunCaps(max_items=10, max_bytes=1_000_000)

    first = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )
    (fixture / "item-3.txt").write_text("Acme acquired a competitor\n", encoding="utf-8")
    second = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )

    observation = json.loads(
        (vault / str(second["observation_candidate_path"])).read_text(encoding="utf-8")
    )
    citations = observation["source_candidate_paths"]
    expected = sorted({*first["source_candidate_paths"], *second["source_candidate_paths"]})
    assert sorted(citations) == expected
    assert len(citations) > 0


# ── 6. CLI watch-collect --url through egress-gated HTTP connector ────────


def _declare_provider(vault: Path, provider: str = "gdelt") -> None:
    config_path = vault / ".constellation/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["egress"]["external_enabled"] = True
    config["egress"]["providers"][provider] = {
        "enabled": True,
        "transport": "local",
        "max_sensitivity": "internal",
        "models": [f"{provider}-live-api"],
        "purposes": ["research"],
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")


def test_watch_collect_url_denied_without_declared_provider(tmp_path: Path, monkeypatch) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    called = []
    monkeypatch.setattr(
        watchlists, "_default_http_fetcher",
        lambda url, timeout: called.append(url) or b"{}",
    )

    values = vars(build_parser().parse_args([
        "watch-collect", str(vault),
        "--watchlist-id", watchlist_id,
        "--url", "https://api.gdeltproject.org/api/v2/doc/doc?query=acme&format=json",
        "--provider", "gdelt",
    ]))
    with pytest.raises(Exception, match="(?i)egress|denied"):
        run_action(str(values.pop("command")), values)
    assert called == []


def test_watch_collect_url_runs_with_declared_provider(tmp_path: Path, monkeypatch) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    _declare_provider(vault)
    monkeypatch.setattr(
        watchlists, "_default_http_fetcher",
        lambda url, timeout: b"2026-07-29 | Acme news | https://example.test/a\n",
    )

    values = vars(build_parser().parse_args([
        "watch-collect", str(vault),
        "--watchlist-id", watchlist_id,
        "--url", "https://api.gdeltproject.org/api/v2/doc/doc?query=acme&format=json",
        "--provider", "gdelt",
    ]))
    result = run_action(str(values.pop("command")), values)

    assert result["status"] == "ok"
    assert result["connector"] == "http"
    assert result["items_fetched"] == 1
    assert (vault / str(result["receipt_path"])).is_file()
    for rel in result["source_candidate_paths"]:
        assert (vault / rel).is_file()


def test_watch_collect_url_rejects_private_address(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    values = vars(build_parser().parse_args([
        "watch-collect", str(vault),
        "--watchlist-id", watchlist_id,
        "--url", "http://localhost:8080/feed",
        "--provider", "gdelt",
    ]))
    with pytest.raises(Exception):
        run_action(str(values.pop("command")), values)


# ── 7. Reviewed Observation → Event transition ────────────────────────────


def test_observation_to_event_reviewed_transition(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    fixture = _fixture_dir(tmp_path)
    caps = watchlists.RunCaps(max_items=10, max_bytes=1_000_000)

    watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )
    (fixture / "item-3.txt").write_text("Acme acquired a competitor\n", encoding="utf-8")
    second = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )

    obs_rel = str(second["observation_candidate_path"])
    obs_payload = json.loads((vault / obs_rel).read_text(encoding="utf-8"))
    obs_id = obs_payload["id"]
    candidate_stem = Path(obs_rel).stem

    promoted = promote_candidate(vault, candidate_stem, confirm=True, expected_base_hash=None)
    assert promoted["status"] == "promoted"
    canonical_obs = vault / "observations" / f"{obs_id}.md"
    assert canonical_obs.is_file()

    event = watchlists.stage_event(
        vault,
        title="Acme acquired a competitor",
        description="Watchlist detected acquisition signal.",
        event_date="2026-07-29",
        event_type="acquisition",
        observation_ids=[obs_id],
    )
    event_stem = Path(str(event["candidate_path"])).stem
    promoted_event = promote_candidate(vault, event_stem, confirm=True, expected_base_hash=None)
    assert promoted_event["status"] == "promoted"

    canonical_event = vault / "events" / f"{event['event_id']}.md"
    assert canonical_event.is_file()
    text = canonical_event.read_text(encoding="utf-8")
    assert obs_id in text


# ── 8. Acceptance: two deterministic runs, one change candidate, no dup ────


def test_w32_acceptance_two_runs_one_candidate_no_duplicate(tmp_path: Path) -> None:
    vault, watchlist_id = _setup_vault(tmp_path)
    fixture = _fixture_dir(tmp_path)
    caps = watchlists.RunCaps(max_items=10, max_bytes=1_000_000)

    first = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )
    (fixture / "item-3.txt").write_text("Acme acquired a competitor\n", encoding="utf-8")
    second = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )
    third = watchlists.run_watchlist(
        vault, watchlist_id=watchlist_id,
        connector=watchlists.LocalFixtureConnector(fixture), caps=caps,
    )

    assert first["snapshot_candidate_path"] != second["snapshot_candidate_path"]
    assert second["material_change"] is True
    observations = list((vault / ".constellation/candidates").glob("observation-*.json"))
    assert len(observations) == 1
    assert third["material_change"] is False
    # all receipts preserved
    receipts = _receipts(vault, watchlist_id)
    assert len(receipts) == 3
    for receipt in receipts:
        assert receipt["normalized_content_sha256"]
        assert hashlib.sha256  # receipts carry hashes, not raw fetched content
        assert "Acme acquired a competitor" not in json.dumps(receipt)
