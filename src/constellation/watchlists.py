"""Watchlists and temporal intelligence — Watchlist, Snapshot, Observation, Event.

Watchlists monitor entities/terms across sources. Snapshots capture point-in-time
state. Observations flag material changes between snapshots. Events anchor
time-based canonical facts from observations.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .http_connector import HttpConnector

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


# ── Connector execution (Step 11) ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RunCaps:
    """Explicit bounds for one watchlist run."""

    max_items: int = 50
    max_bytes: int = 5_000_000


@dataclass(frozen=True, slots=True)
class ConnectorItem:
    label: str
    text: str
    source_path: Path


class WatchConnector(Protocol):
    """Provider-neutral connector interface."""

    def name(self) -> str: ...

    def fetch(self, caps: RunCaps) -> tuple[list[ConnectorItem], bool]: ...


class LocalFixtureConnector:
    """Deterministic local fixture connector reading *.txt from a directory."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    def name(self) -> str:
        return "local-fixture"

    def fetch(self, caps: RunCaps) -> tuple[list[ConnectorItem], bool]:
        if not self._directory.is_dir():
            raise WatchlistError(f"fixture directory not found: {self._directory}")
        items: list[ConnectorItem] = []
        truncated = False
        total_bytes = 0
        for path in sorted(self._directory.glob("*.txt")):
            if path.is_symlink() or not path.is_file():
                continue
            if len(items) >= caps.max_items:
                truncated = True
                break
            data = path.read_bytes()
            if total_bytes + len(data) > caps.max_bytes:
                truncated = True
                break
            total_bytes += len(data)
            items.append(ConnectorItem(
                label=path.name,
                text=data.decode("utf-8", errors="replace"),
                source_path=path,
            ))
        return items, truncated


# ── HTTP connector wiring (W3.2) ─────────────────────────────────────────


def _default_http_fetcher(url: str, timeout: int) -> bytes:
    """Production fetcher for HttpConnector; monkeypatched in tests."""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "constellation-watch/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def make_http_connector(
    vault: Path | str,
    urls: list[str],
    *,
    provider: str,
    model: str,
    sensitivity: Sensitivity,
    fetcher: Callable[[str, int], bytes] | None = None,
) -> HttpConnector:
    """Build an HttpConnector authorized through the vault egress policy.

    Authorization is evaluated (and ledgered) per URL BEFORE any network
    attempt; an undeclared provider or disallowed purpose fails closed with
    WatchlistError and zero network traffic.
    """
    from .egress import EgressRequest, authorize_egress
    from .http_connector import HttpConnector

    vault = Path(vault).absolute()

    def authorize(url: str) -> None:
        decision = authorize_egress(
            vault,
            EgressRequest(
                provider=provider,
                model=model,
                purpose="research",
                sensitivity=sensitivity,
                request_input_sha256=hashlib.sha256(url.encode()).hexdigest(),
            ),
        )
        if not decision.allowed:
            raise WatchlistError(f"egress denied for {provider} watch fetch: {decision.reason}")

    return HttpConnector(urls, fetcher=fetcher or _default_http_fetcher, authorize=authorize)


def _write_failed_receipt(
    vault: Path,
    *,
    watchlist_id: str,
    connector_name: str,
    error_type: str,
    caps: RunCaps,
) -> str:
    receipt_id = generate_ulid()
    receipt_rel = Path(".constellation/watchlist-runs") / f"failed-{receipt_id}.json"
    atomic_write_text(
        vault,
        receipt_rel,
        json.dumps(
            {
                "schema_version": "0.1",
                "status": "failed",
                "watchlist_id": watchlist_id,
                "connector": connector_name,
                "error": error_type,
                "caps": {"max_items": caps.max_items, "max_bytes": caps.max_bytes},
                "recorded_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return receipt_rel.as_posix()


_COMPARABLE_STATUSES = {"ok", "partial", "duplicate_change"}


def _scan_run_receipts(
    vault: Path, watchlist_id: str
) -> tuple[list[tuple[str, dict[str, object]]], list[str]]:
    """Return (valid receipts for the watchlist, skipped corrupt filenames)."""
    runs_dir = vault / ".constellation/watchlist-runs"
    valid: list[tuple[str, dict[str, object]]] = []
    skipped: list[str] = []
    if not runs_dir.is_dir():
        return valid, skipped
    for path in sorted(runs_dir.glob("*.json")):
        if path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skipped.append(path.name)
            continue
        if not isinstance(data, dict):
            skipped.append(path.name)
            continue
        if data.get("watchlist_id") != watchlist_id:
            continue
        valid.append((path.name, data))
    return valid, skipped


def _resolve_latest_receipt(
    vault: Path, watchlist_id: str
) -> tuple[str | None, dict[str, object] | None, list[str]]:
    """Resolve the newest comparable prior run for automatic comparison.

    Deterministic: max by (recorded_at, snapshot_id). Corrupt receipts are
    skipped and reported so the run can flag itself degraded instead of
    silently comparing against nothing.
    """
    valid, skipped = _scan_run_receipts(vault, watchlist_id)
    comparable = [
        (name, data)
        for name, data in valid
        if data.get("status") in _COMPARABLE_STATUSES
        and data.get("snapshot_id")
        and data.get("normalized_content_sha256")
    ]
    if not comparable:
        return None, None, skipped
    name, receipt = max(
        comparable,
        key=lambda item: (str(item[1].get("recorded_at", "")), str(item[1].get("snapshot_id", ""))),
    )
    return str(receipt["snapshot_id"]), receipt, skipped


def run_watchlist(
    vault: Path | str,
    *,
    watchlist_id: str,
    connector: WatchConnector,
    caps: RunCaps | None = None,
    previous_snapshot_id: str | None = None,
) -> dict[str, object]:
    """Execute one bounded connector-driven watchlist run.

    Preserves fetched items as SourceItem candidates, stages a deterministic
    snapshot, and emits at most one review-only Observation per logical
    change. Sources remain candidates — promotion stays owner-gated.
    """
    from .ingest import ingest_file

    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise WatchlistError("vault is not initialized")
    _require_canonical_watchlist(vault, watchlist_id)
    caps = caps or RunCaps()

    receipt_rel_base = Path(".constellation/watchlist-runs")

    try:
        items, truncated = connector.fetch(caps)
    except WatchlistError:
        raise
    except Exception as exc:
        _write_failed_receipt(
            vault,
            watchlist_id=watchlist_id,
            connector_name=connector.name(),
            error_type=type(exc).__name__,
            caps=caps,
        )
        raise WatchlistError(f"connector fetch failed: {connector.name()}") from exc

    if not items:
        receipt_id = generate_ulid()
        receipt_rel = receipt_rel_base / f"empty-{receipt_id}.json"
        atomic_write_text(
            vault,
            receipt_rel,
            json.dumps(
                {
                    "schema_version": "0.1",
                    "status": "empty",
                    "watchlist_id": watchlist_id,
                    "connector": connector.name(),
                    "items_fetched": 0,
                    "caps": {"max_items": caps.max_items, "max_bytes": caps.max_bytes},
                    "recorded_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        return {
            "status": "empty",
            "items_fetched": 0,
            "snapshot_candidate_path": None,
            "receipt_path": receipt_rel.as_posix(),
        }

    source_candidate_paths: list[str] = []
    inbox_id = generate_ulid()
    for item in items:
        inbox_rel = Path(".constellation/watchlist-inbox") / inbox_id / item.label
        atomic_write_text(vault, inbox_rel, item.text)
        ingested = ingest_file(vault, vault / inbox_rel, kind="generic")
        candidate_path = ingested.get("candidate_path")
        if candidate_path:
            source_candidate_paths.append(str(candidate_path))

    aggregated = "\n".join(item.text for item in items)
    normalized = _normalize_snapshot_content(aggregated)
    if not normalized:
        raise WatchlistError("snapshot content is empty after normalization")
    content_hash = hashlib.sha256(normalized.encode()).hexdigest()

    skipped_receipts: list[str] = []
    if previous_snapshot_id:
        previous_receipt = _load_previous_receipt(vault, watchlist_id, previous_snapshot_id)
    else:
        resolved_id, previous_receipt, skipped_receipts = _resolve_latest_receipt(
            vault, watchlist_id
        )
        previous_snapshot_id = resolved_id
    degraded = bool(skipped_receipts)

    snapshot = stage_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=[],
        preserved_content=normalized,
        previous_snapshot_id=previous_snapshot_id,
    )
    snapshot_id = str(snapshot["snapshot_id"])
    change = _resolve_material_change(
        vault,
        watchlist_id=watchlist_id,
        snapshot_id=snapshot_id,
        previous_snapshot_id=previous_snapshot_id,
        previous_receipt=previous_receipt,
        content_hash=content_hash,
        source_ids=[],
        source_candidate_paths=source_candidate_paths,
    )
    material_change = bool(change["material_change"])
    duplicate_change = bool(change["duplicate_change"])

    status = "duplicate_change" if duplicate_change else "partial" if truncated else "ok"
    receipt_rel = receipt_rel_base / f"{snapshot_id}.json"
    receipt = {
        "schema_version": "0.1",
        "status": status,
        "watchlist_id": watchlist_id,
        "snapshot_id": snapshot_id,
        "snapshot_candidate_path": snapshot["candidate_path"],
        "connector": connector.name(),
        "items_fetched": len(items),
        "truncated": truncated,
        "caps": {"max_items": caps.max_items, "max_bytes": caps.max_bytes},
        "source_state": "candidate",
        "source_candidate_paths": source_candidate_paths,
        "source_ids": [],
        "normalized_content_sha256": content_hash,
        "previous_snapshot_id": previous_snapshot_id,
        "material_change": material_change,
        "duplicate_change": duplicate_change,
        "change_key": change["change_key"],
        "observation_candidate_path": change["observation_candidate_path"],
        "degraded": degraded,
        "skipped_receipts": skipped_receipts,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(vault, receipt_rel, json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    return {
        "status": status,
        "snapshot_id": snapshot_id,
        "snapshot_candidate_path": snapshot["candidate_path"],
        "receipt_path": receipt_rel.as_posix(),
        "items_fetched": len(items),
        "truncated": truncated,
        "connector": connector.name(),
        "source_state": "candidate",
        "source_candidate_paths": source_candidate_paths,
        "material_change": material_change,
        "duplicate_change": duplicate_change,
        "degraded": degraded,
        "observation_candidate_path": change["observation_candidate_path"],
    }


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


def _load_previous_receipt(
    vault: Path, watchlist_id: str, previous_snapshot_id: str | None
) -> dict[str, object] | None:
    if not previous_snapshot_id:
        return None
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
    return previous_receipt


def _resolve_material_change(
    vault: Path,
    *,
    watchlist_id: str,
    snapshot_id: str,
    previous_snapshot_id: str | None,
    previous_receipt: dict[str, object] | None,
    content_hash: str,
    source_ids: list[str],
    source_candidate_paths: list[str] | None = None,
) -> dict[str, object]:
    """Stage (or dedup) the review-only Observation for a changed snapshot."""
    result: dict[str, object] = {
        "material_change": False,
        "duplicate_change": False,
        "change_key": None,
        "observation_candidate_path": None,
    }
    if previous_receipt is None:
        return result
    previous_hash = str(previous_receipt.get("normalized_content_sha256", ""))
    if previous_hash == content_hash:
        return result

    result["material_change"] = True
    previous_source_ids = previous_receipt.get("source_ids", [])
    if not isinstance(previous_source_ids, list):
        raise WatchlistError(f"previous snapshot receipt is invalid: {previous_snapshot_id}")
    evidence_source_ids = sorted({*source_ids, *[str(item) for item in previous_source_ids]})
    previous_candidates = previous_receipt.get("source_candidate_paths", [])
    if not isinstance(previous_candidates, list):
        previous_candidates = []
    evidence_candidate_paths: list[str] = sorted(
        {*(source_candidate_paths or []), *[str(item) for item in previous_candidates]}
    )
    change_key = hashlib.sha256(
        f"{watchlist_id}\0{previous_hash}\0{content_hash}".encode()
    ).hexdigest()
    result["change_key"] = change_key
    marker_rel = Path(".constellation/watchlist-runs/material-changes") / f"{change_key}.json"
    marker_path = vault / marker_rel
    if marker_path.exists() or marker_path.is_symlink():
        result["observation_candidate_path"] = _existing_marker_observation_path(marker_path, change_key)
        result["duplicate_change"] = True
        return result

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
        result["observation_candidate_path"] = _existing_marker_observation_path(marker_path, change_key)
        result["duplicate_change"] = True
        return result

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
        source_candidate_paths=evidence_candidate_paths,
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
    result["observation_candidate_path"] = observation_candidate_path
    return result


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

    previous_receipt = _load_previous_receipt(vault, watchlist_id, previous_snapshot_id)

    snapshot = stage_snapshot(
        vault,
        watchlist_id=watchlist_id,
        source_ids=source_ids,
        preserved_content=normalized,
        previous_snapshot_id=previous_snapshot_id,
    )
    snapshot_id = str(snapshot["snapshot_id"])
    change = _resolve_material_change(
        vault,
        watchlist_id=watchlist_id,
        snapshot_id=snapshot_id,
        previous_snapshot_id=previous_snapshot_id,
        previous_receipt=previous_receipt,
        content_hash=content_hash,
        source_ids=source_ids,
    )
    material_change = bool(change["material_change"])
    duplicate_change = bool(change["duplicate_change"])
    change_key = change["change_key"]
    observation_candidate_path = change["observation_candidate_path"]

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
    source_candidate_paths: list[str] | None = None,
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
        source_candidate_paths=source_candidate_paths or [],
    )
    candidate_rel = Path(".constellation/candidates") / f"observation-{obs.id}.json"
    atomic_write_text(vault, candidate_rel, obs.model_dump_json(indent=2) + "\n")
    return {"status": "staged", "observation_id": obs.id, "candidate_path": candidate_rel.as_posix()}


# ── Watch-status reporting (W3.2) ────────────────────────────────────────


def watch_status(
    vault: Path | str,
    *,
    watchlist_id: str | None = None,
    stale_after_hours: float = 24.0,
) -> dict[str, object]:
    """Report per-watchlist run state: no_runs / fresh / stale / degraded.

    Read-only. A corrupt run receipt degrades every entry because automatic
    previous-snapshot resolution can no longer be trusted vault-wide.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise WatchlistError("vault is not initialized")

    watchlists_dir = vault / "watchlists"
    records: list[Watchlist] = []
    if watchlists_dir.is_dir():
        for path in sorted(watchlists_dir.glob("*.md")):
            if path.is_symlink():
                continue
            try:
                metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
                record = Watchlist.model_validate(metadata, strict=False)
            except Exception:
                continue
            if watchlist_id and record.id != watchlist_id:
                continue
            records.append(record)

    now = datetime.now(UTC)
    entries: list[dict[str, object]] = []
    for record in records:
        valid, skipped = _scan_run_receipts(vault, record.id)
        comparable = [
            data
            for _, data in valid
            if data.get("status") in _COMPARABLE_STATUSES and data.get("snapshot_id")
        ]
        latest: dict[str, object] | None = None
        if comparable:
            latest = max(
                comparable,
                key=lambda d: (str(d.get("recorded_at", "")), str(d.get("snapshot_id", ""))),
            )
        entry: dict[str, object] = {
            "watchlist_id": record.id,
            "title": record.title,
            "schedule": record.schedule,
            "last_snapshot_id": str(latest["snapshot_id"]) if latest else None,
            "last_recorded_at": str(latest.get("recorded_at")) if latest else None,
            "last_status": str(latest.get("status")) if latest else None,
            "skipped_receipts": skipped,
        }
        if skipped:
            entry["state"] = "degraded"
        elif latest is None:
            entry["state"] = "no_runs"
        else:
            try:
                recorded = datetime.fromisoformat(str(latest["recorded_at"]))
                age_hours = (now - recorded).total_seconds() / 3600.0
            except (ValueError, TypeError):
                age_hours = float("inf")
            entry["age_hours"] = round(age_hours, 3) if age_hours != float("inf") else None
            entry["state"] = "fresh" if age_hours <= stale_after_hours else "stale"
        entries.append(entry)

    return {
        "status": "ok",
        "stale_after_hours": stale_after_hours,
        "watchlists": entries,
    }


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
