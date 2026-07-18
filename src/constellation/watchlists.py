"""Watchlists and temporal intelligence — Watchlist, Snapshot, Observation, Event.

Watchlists monitor entities/terms across sources. Snapshots capture point-in-time
state. Observations flag material changes between snapshots. Events anchor
time-based canonical facts from observations.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    Event,
    Observation,
    Sensitivity,
    Snapshot,
    Watchlist,
    generate_ulid,
)
from .storage import atomic_write_text
from .vault import is_initialized


class WatchlistError(RuntimeError):
    """Raised when watchlist operations fail."""


# ── Watchlist ────────────────────────────────────────────────────────────


def stage_watchlist(
    vault: Path | str,
    *,
    title: str,
    entity_ids: list[str] | None = None,
    query_terms: list[str] | None = None,
    sources: list[str] | None = None,
    schedule: str = "",
) -> dict[str, object]:
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise WatchlistError("vault is not initialized")

    now = datetime.now(UTC)
    wl = Watchlist(
        id=generate_ulid(),
        title=title,
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
        entity_ids=entity_ids or [],
        query_terms=query_terms or [],
        sources=sources or [],
        schedule=schedule,
    )
    candidate_rel = Path(".constellation/candidates") / f"watchlist-{wl.id}.json"
    atomic_write_text(vault, candidate_rel, wl.model_dump_json(indent=2) + "\n")
    return {"status": "staged", "watchlist_id": wl.id, "candidate_path": candidate_rel.as_posix()}


# ── Snapshot ─────────────────────────────────────────────────────────────


def stage_snapshot(
    vault: Path | str,
    *,
    watchlist_id: str,
    source_ids: list[str] | None = None,
    preserved_content: str = "",
    previous_snapshot_id: str | None = None,
) -> dict[str, object]:
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise WatchlistError("vault is not initialized")

    source_hash = hashlib.sha256(preserved_content.encode()).hexdigest() if preserved_content else ""

    now = datetime.now(UTC)
    snap = Snapshot(
        id=generate_ulid(),
        title=f"snapshot-{watchlist_id[:8]}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
        watchlist_id=watchlist_id,
        source_ids=source_ids or [],
        material_diff_from=previous_snapshot_id,
        preserved_bytes_sha256=source_hash,
    )
    candidate_rel = Path(".constellation/candidates") / f"snapshot-{snap.id}.json"
    atomic_write_text(vault, candidate_rel, snap.model_dump_json(indent=2) + "\n")
    return {"status": "staged", "snapshot_id": snap.id, "candidate_path": candidate_rel.as_posix()}


# ── Observation ──────────────────────────────────────────────────────────


def stage_observation(
    vault: Path | str,
    *,
    watchlist_id: str,
    snapshot_id: str,
    change_summary: str,
    previous_snapshot_id: str | None = None,
    entity_ids: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, object]:
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise WatchlistError("vault is not initialized")

    now = datetime.now(UTC)
    obs = Observation(
        id=generate_ulid(),
        title=f"observation-{snapshot_id[:8]}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
        watchlist_id=watchlist_id,
        snapshot_id=snapshot_id,
        previous_snapshot_id=previous_snapshot_id,
        change_summary=change_summary,
        entity_ids=entity_ids or [],
        source_ids=source_ids or [],
    )
    candidate_rel = Path(".constellation/candidates") / f"observation-{obs.id}.json"
    atomic_write_text(vault, candidate_rel, obs.model_dump_json(indent=2) + "\n")
    return {"status": "staged", "observation_id": obs.id, "candidate_path": candidate_rel.as_posix()}


# ── Event ────────────────────────────────────────────────────────────────


def stage_event(
    vault: Path | str,
    *,
    title: str,
    description: str,
    entity_ids: list[str] | None = None,
    event_date: str = "",
    event_type: str = "general",
    observation_ids: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, object]:
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise WatchlistError("vault is not initialized")

    now = datetime.now(UTC)
    evt = Event(
        id=generate_ulid(),
        title=title,
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=now,
        updated_at=now,
        entity_ids=entity_ids or [],
        event_date=event_date,
        event_type=event_type,
        description=description,
        observation_ids=observation_ids or [],
        source_ids=source_ids or [],
    )
    candidate_rel = Path(".constellation/candidates") / f"event-{evt.id}.json"
    atomic_write_text(vault, candidate_rel, evt.model_dump_json(indent=2) + "\n")
    return {"status": "staged", "event_id": evt.id, "candidate_path": candidate_rel.as_posix()}
