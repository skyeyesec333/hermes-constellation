import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from constellation.backfill_state import BackfillStateError, recover_expired_leases

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def _write_state(path: Path, record: dict[str, object]) -> None:
    path.write_text(json.dumps({"version": 1, "records": [record]}) + "\n", encoding="utf-8")


def _record(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))["records"][0]


def test_fresh_backfill_lease_remains_locked(tmp_path):
    path = tmp_path / "state.json"
    _write_state(path, {
        "subject_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "status": "in_progress",
        "lease_owner": "worker-a",
        "lease_started_at": (NOW - timedelta(minutes=5)).isoformat(),
    })

    result = recover_expired_leases(path, now=NOW, lease_seconds=3600)

    assert result["recovered"] == 0
    assert _record(path)["status"] == "in_progress"


def test_expired_backfill_lease_returns_to_pending(tmp_path):
    path = tmp_path / "state.json"
    _write_state(path, {
        "subject_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "status": "in_progress",
        "lease_owner": "dead-worker",
        "lease_started_at": (NOW - timedelta(hours=2)).isoformat(),
    })

    result = recover_expired_leases(path, now=NOW, lease_seconds=3600)

    assert result["recovered"] == 1
    assert _record(path)["status"] == "pending"


def test_completed_backfill_record_never_reopens(tmp_path):
    path = tmp_path / "state.json"
    _write_state(path, {
        "subject_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "status": "completed",
        "lease_owner": "old-worker",
        "lease_started_at": (NOW - timedelta(days=2)).isoformat(),
    })

    recover_expired_leases(path, now=NOW, lease_seconds=3600)

    assert _record(path)["status"] == "completed"


def test_backfill_recovery_is_atomic_on_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    _write_state(path, {
        "subject_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "status": "in_progress",
        "lease_owner": "dead-worker",
        "lease_started_at": (NOW - timedelta(hours=2)).isoformat(),
    })
    original = path.read_bytes()
    monkeypatch.setattr("constellation.backfill_state.os.replace", lambda *_: (_ for _ in ()).throw(OSError("stop")))

    with pytest.raises(OSError, match="stop"):
        recover_expired_leases(path, now=NOW, lease_seconds=3600)

    assert path.read_bytes() == original


def test_backfill_recovery_is_idempotent(tmp_path):
    path = tmp_path / "state.json"
    _write_state(path, {
        "subject_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "status": "in_progress",
        "lease_owner": "dead-worker",
        "lease_started_at": (NOW - timedelta(hours=2)).isoformat(),
    })

    first = recover_expired_leases(path, now=NOW, lease_seconds=3600)
    after_first = path.read_bytes()
    second = recover_expired_leases(path, now=NOW, lease_seconds=3600)

    assert first["recovered"] == 1
    assert second["recovered"] == 0
    assert path.read_bytes() == after_first


def test_backfill_recovery_dry_run_reports_expired_leases_without_writing(tmp_path):
    path = tmp_path / "state.json"
    _write_state(path, {
        "subject_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "status": "in_progress",
        "lease_owner": "dead-worker",
        "lease_started_at": (NOW - timedelta(hours=2)).isoformat(),
    })
    original = path.read_bytes()
    directory_before = sorted(entry.name for entry in tmp_path.iterdir())

    result = recover_expired_leases(path, now=NOW, lease_seconds=3600, dry_run=True)

    assert result["status"] == "would_recover"
    assert result["recovered"] == 1
    assert path.read_bytes() == original
    assert sorted(entry.name for entry in tmp_path.iterdir()) == directory_before


@pytest.mark.parametrize(
    "lease_owner,lease_started_at",
    [
        (None, (NOW - timedelta(hours=2)).isoformat()),
        ("", (NOW - timedelta(hours=2)).isoformat()),
        ("worker-a", "not-a-timestamp"),
    ],
)
def test_backfill_recovery_rejects_malformed_active_lease_metadata(
    tmp_path, lease_owner, lease_started_at
):
    path = tmp_path / "state.json"
    _write_state(path, {
        "subject_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "status": "in_progress",
        "lease_owner": lease_owner,
        "lease_started_at": lease_started_at,
    })
    original = path.read_bytes()

    with pytest.raises(BackfillStateError, match="lease metadata"):
        recover_expired_leases(path, now=NOW, lease_seconds=3600)

    assert path.read_bytes() == original


def test_backfill_recovery_rejects_symlink_lock_path(tmp_path):
    path = tmp_path / "state.json"
    _write_state(path, {
        "subject_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "status": "in_progress",
        "lease_owner": "dead-worker",
        "lease_started_at": (NOW - timedelta(hours=2)).isoformat(),
    })
    protected = tmp_path / "protected.txt"
    protected.write_text("do not follow\n", encoding="utf-8")
    (tmp_path / ".state.json.lock").symlink_to(protected)

    with pytest.raises(BackfillStateError, match="lock path"):
        recover_expired_leases(path, now=NOW, lease_seconds=3600)

    assert protected.read_text(encoding="utf-8") == "do not follow\n"
