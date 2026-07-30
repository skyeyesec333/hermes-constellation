"""Atomic stale-lease recovery for resumable backfill state files."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class BackfillStateError(ValueError):
    pass


def _open_regular_no_follow(path: Path, flags: int, *, label: str) -> int:
    """Open a regular file without following a final-component symlink."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise BackfillStateError(f"{label} cannot be opened safely on this platform")
    try:
        descriptor = os.open(path, flags | no_follow, 0o600)
    except OSError as exc:
        raise BackfillStateError(f"{label} is unreadable or unsafe") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BackfillStateError(f"{label} must be a regular file")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _aware_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _atomic_json_replace(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=False) + "\n").encode("utf-8")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _read_state_payload(path: Path) -> dict[str, Any]:
    try:
        state_descriptor = _open_regular_no_follow(path, os.O_RDONLY, label="state path")
        with os.fdopen(state_descriptor, "r", encoding="utf-8") as state:
            payload = json.load(state)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackfillStateError("state file is unreadable") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise BackfillStateError("state file must contain a records list")
    return payload


def _recover_records(
    payload: dict[str, Any], *, instant: datetime, lease_seconds: int
) -> int:
    recovered = 0
    for record in payload["records"]:
        if not isinstance(record, dict):
            raise BackfillStateError("state records must be objects")
        if record.get("status") != "in_progress":
            continue
        started = _aware_timestamp(record.get("lease_started_at"))
        owner = record.get("lease_owner")
        if started is None or not isinstance(owner, str) or not owner.strip():
            raise BackfillStateError("in-progress lease metadata is invalid")
        if started + timedelta(seconds=lease_seconds) > instant:
            continue
        record["status"] = "pending"
        record.pop("lease_owner", None)
        record.pop("lease_started_at", None)
        record["recovered_at"] = instant.isoformat()
        record["recovery_count"] = int(record.get("recovery_count", 0)) + 1
        recovered += 1
    return recovered


def recover_expired_leases(
    state_path: Path | str,
    *,
    now: datetime | None = None,
    lease_seconds: int = 3600,
    dry_run: bool = False,
) -> dict[str, object]:
    """Atomically return expired ``in_progress`` records to ``pending``."""
    path = Path(state_path).absolute()
    if lease_seconds < 1:
        raise BackfillStateError("lease_seconds must be positive")
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise BackfillStateError("now must include a timezone")
    if path.is_symlink() or not path.is_file():
        raise BackfillStateError("state path must be a regular file")

    if dry_run:
        payload = _read_state_payload(path)
        recovered = _recover_records(payload, instant=instant, lease_seconds=lease_seconds)
    else:
        lock_path = path.with_name(f".{path.name}.lock")
        lock_descriptor = _open_regular_no_follow(
            lock_path, os.O_RDWR | os.O_CREAT, label="lock path"
        )
        with os.fdopen(lock_descriptor, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                payload = _read_state_payload(path)
                recovered = _recover_records(payload, instant=instant, lease_seconds=lease_seconds)
                if recovered:
                    _atomic_json_replace(path, payload)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return {
        "schema_version": "0.1",
        "status": (
            "would_recover" if dry_run and recovered
            else "recovered" if recovered
            else "unchanged"
        ),
        "recovered": recovered,
        "state_path": str(path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover expired Constellation backfill leases")
    parser.add_argument("state_path", type=Path)
    parser.add_argument("--lease-seconds", type=int, default=3600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = recover_expired_leases(
        args.state_path,
        lease_seconds=args.lease_seconds,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
