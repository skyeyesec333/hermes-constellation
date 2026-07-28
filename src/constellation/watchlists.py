"""Watchlists and temporal intelligence — Watchlist, Snapshot, Observation, Event.

Watchlists monitor entities/terms across sources. Snapshots capture point-in-time
state. Observations flag material changes between snapshots. Events anchor
time-based canonical facts from observations.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .frontmatter import parse_frontmatter
from .models import (
    Event,
    Observation,
    Sensitivity,
    Snapshot,
    SourceItem,
    Watchlist,
    generate_ulid,
)
from .storage import ConflictError, atomic_write_text, sha256_file
from .vault import is_initialized


class WatchlistError(RuntimeError):
    """Raised when watchlist operations fail."""


def _require_canonical_watchlist(vault: Path, watchlist_id: str) -> Watchlist:
    path = vault / "watchlists" / f"{watchlist_id}.md"
    if not path.is_file() or path.is_symlink():
        raise WatchlistError(f"canonical watchlist not found: {watchlist_id}")
    try:
        metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        record = Watchlist.model_validate(metadata, strict=False)
    except Exception as exc:
        raise WatchlistError(f"canonical watchlist is invalid: {watchlist_id}") from exc
    if record.id != watchlist_id:
        raise WatchlistError(f"watchlist ID mismatch: {watchlist_id}")
    return record


def _require_canonical_sources(vault: Path, source_ids: list[str]) -> None:
    if not source_ids:
        raise WatchlistError("at least one canonical source ID is required")
    for source_id in source_ids:
        path = vault / "source-items" / f"{source_id}.md"
        if not path.is_file() or path.is_symlink():
            raise WatchlistError(f"canonical source item not found: {source_id}")
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            record = SourceItem.model_validate(metadata, strict=False)
        except Exception as exc:
            raise WatchlistError(f"canonical source item is invalid: {source_id}") from exc
        if record.id != source_id:
            raise WatchlistError(f"source item ID mismatch: {source_id}")


def _normalize_snapshot_content(content: str) -> str:
    lines = [line.strip() for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def _existing_marker_observation_path(marker_path: Path, change_key: str) -> str:
    """Return the observation path for a complete marker; fail closed otherwise."""
    if marker_path.is_symlink():
        raise WatchlistError(f"material-change marker is invalid: {change_key}")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WatchlistError(f"material-change marker is invalid: {change_key}") from exc
    if not isinstance(marker, dict):
        raise WatchlistError(f"material-change marker is invalid: {change_key}")
    observation_path = marker.get("observation_candidate_path")
    if marker.get("status") == "pending" or observation_path is None:
        raise WatchlistError(
            f"material-change reservation is incomplete for change {change_key}; "
            "a prior run may have crashed after reserving the change. Remove "
            f".constellation/watchlist-runs/material-changes/{change_key}.json after "
            "verifying no observation candidate was staged for it, then rerun."
        )
    if not isinstance(observation_path, str):
        raise WatchlistError(f"material-change marker is invalid: {change_key}")
    return observation_path


def execute_watchlist_snapshot(
    vault: Path | str,
    *,
    watchlist_id: str,
    source_ids: list[str],
    preserved_content: str,
    previous_snapshot_id: str | None = None,
) -> dict[str, object]:
    """Stage one deterministic, source-grounded snapshot and terminal receipt."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise WatchlistError("vault is not initialized")
    _require_canonical_watchlist(vault, watchlist_id)
    _require_canonical_sources(vault, source_ids)
    source_ids = sorted(source_ids)

    normalized = _normalize_snapshot_content(preserved_content)
    if not normalized:
        raise WatchlistError("snapshot content is empty after normalization")
    content_hash = hashlib.sha256(normalized.encode()).hexdigest()

    previous_receipt: dict[str, object] | None = None
    if previous_snapshot_id:
        previous_path = vault / ".constellation/watchlist-runs" / f"{previous_snapshot_id}.json"
        if not previous_path.is_file() or previous_path.is_symlink():
            raise WatchlistError(f"previous snapshot receipt not found: {previous_snapshot_id}")
        try:
            previous_receipt = json.loads(previous_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WatchlistError(f"previous snapshot receipt is invalid: {previous_snapshot_id}") from exc
        if not isinstance(previous_receipt, dict):
            raise WatchlistError(f"previous snapshot receipt is invalid: {previous_snapshot_id}")
        if previous_receipt.get("watchlist_id") != watchlist_id:
            raise WatchlistError("previous snapshot belongs to a different watchlist")

    snapshot = stage_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=source_ids,
        preserved_content=normalized,
        previous_snapshot_id=previous_snapshot_id,
    )
    snapshot_id = str(snapshot["snapshot_id"])
    material_change = bool(
        previous_receipt
        and previous_receipt.get("normalized_content_sha256") != content_hash
    )
    observation_candidate_path: str | None = None
    duplicate_change = False
    change_key: str | None = None
    if material_change and previous_receipt is not None:
        previous_source_ids = previous_receipt.get("source_ids", [])
        if not isinstance(previous_source_ids, list):
            raise WatchlistError(f"previous snapshot receipt is invalid: {previous_snapshot_id}")
        evidence_source_ids = sorted({*source_ids, *[str(item) for item in previous_source_ids]})
        previous_hash = str(previous_receipt.get("normalized_content_sha256", ""))
        change_key = hashlib.sha256(
            f"{watchlist_id}\0{previous_hash}\0{content_hash}".encode()
        ).hexdigest()
        marker_rel = Path(".constellation/watchlist-runs/material-changes") / f"{change_key}.json"
        marker_path = vault / marker_rel
        if marker_path.exists() or marker_path.is_symlink():
            observation_candidate_path = _existing_marker_observation_path(marker_path, change_key)
            duplicate_change = True
        else:
            pending_payload = {
                "schema_version": "0.1",
                "status": "pending",
                "change_key": change_key,
                "watchlist_id": watchlist_id,
                "previous_content_sha256": previous_hash,
                "current_content_sha256": content_hash,
                "reserving_snapshot_id": snapshot_id,
                "observation_candidate_path": None,
            }
            try:
                atomic_write_text(
                    vault,
                    marker_rel,
                    json.dumps(pending_payload, indent=2, sort_keys=True) + "\n",
                    must_not_exist=True,
                )
            except ConflictError:
                observation_candidate_path = _existing_marker_observation_path(marker_path, change_key)
                duplicate_change = True
            else:
                observation = stage_observation(
                    vault,
                    watchlist_id=watchlist_id,
                    snapshot_id=snapshot_id,
                    previous_snapshot_id=previous_snapshot_id,
                    change_summary=(
                        "Snapshot content changed from "
                        f"{previous_hash[:12]} to {content_hash[:12]}"
                    ),
                    source_ids=evidence_source_ids,
                )
                observation_candidate_path = str(observation["candidate_path"])
                complete_payload = {
                    **pending_payload,
                    "status": "complete",
                    "observation_candidate_path": observation_candidate_path,
                }
                atomic_write_text(
                    vault,
                    marker_rel,
                    json.dumps(complete_payload, indent=2, sort_keys=True) + "\n",
                    expected_hash=sha256_file(marker_path),
                )

    status = (
        "duplicate_change"
        if duplicate_change
        else "material_change_staged"
        if material_change
        else "snapshot_staged"
    )
    receipt_rel = Path(".constellation/watchlist-runs") / f"{snapshot_id}.json"
    receipt = {
        "schema_version": "0.1",
        "status": status,
        "watchlist_id": watchlist_id,
        "snapshot_id": snapshot_id,
        "snapshot_candidate_path": snapshot["candidate_path"],
        "source_ids": source_ids,
        "normalized_content_sha256": content_hash,
        "previous_snapshot_id": previous_snapshot_id,
        "material_change": material_change,
        "duplicate_change": duplicate_change,
        "change_key": change_key,
        "observation_candidate_path": observation_candidate_path,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(vault, receipt_rel, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return {
        "status": status,
        "snapshot_id": snapshot_id,
        "snapshot_candidate_path": snapshot["candidate_path"],
        "receipt_path": receipt_rel.as_posix(),
        "material_change": material_change,
        "duplicate_change": duplicate_change,
        "observation_candidate_path": observation_candidate_path,
    }


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
