"""Explicit, conflict-safe review and candidate promotion."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .models import CandidatePatch
from .storage import ConflictError, atomic_write_text, safe_relative_path, sha256_file
from .validation import CanonicalValidationError, validate_canonical_text
from .vault import is_initialized


class PromotionError(RuntimeError):
    pass


def write_candidate(root: Path | str, candidate: CandidatePatch) -> Path:
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise PromotionError("vault is not initialized")
    relative = Path(".constellation/candidates") / f"{candidate.id}.json"
    content = candidate.model_dump_json(indent=2) + "\n"
    return atomic_write_text(vault, relative, content)


def _candidate_files(root: Path) -> list[Path]:
    directory = safe_relative_path(root, ".constellation/candidates")
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and not path.is_symlink() and path.suffix == ".json"),
        key=lambda path: path.name,
    )


def list_candidates(root: Path | str) -> list[dict[str, object]]:
    vault = Path(root).absolute()
    results: list[dict[str, object]] = []
    for path in _candidate_files(vault):
        try:
            candidate = CandidatePatch.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError):
            continue
        results.append(
            {
                "id": candidate.id,
                "title": candidate.title,
                "target_path": candidate.target_path,
                "expected_base_hash": candidate.expected_base_hash,
            }
        )
    return results


def _load_candidate(root: Path, candidate_id: str) -> tuple[CandidatePatch, Path]:
    path = safe_relative_path(root, Path(".constellation/candidates") / f"{candidate_id}.json")
    if not path.is_file() or path.is_symlink():
        raise PromotionError("candidate does not exist")
    try:
        return CandidatePatch.model_validate_json(path.read_text(encoding="utf-8")), path
    except (ValidationError, ValueError) as exc:
        raise PromotionError("candidate packet is invalid") from exc


def _append_action(root: Path, event: dict[str, object]) -> None:
    ledger = safe_relative_path(root, ".constellation/action-ledger.jsonl")
    payload = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    descriptor = os.open(ledger, flags, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def promote_candidate(
    root: Path | str,
    candidate_id: str,
    *,
    confirm: bool,
    expected_base_hash: str | None,
) -> dict[str, str]:
    if not confirm:
        raise PromotionError("explicit confirmation is required")
    vault = Path(root).absolute()
    candidate, candidate_path = _load_candidate(vault, candidate_id)
    if candidate.expected_base_hash != expected_base_hash:
        raise PromotionError("expected base hash does not match candidate review")
    try:
        validate_canonical_text(candidate.content, candidate.target_path)
        target = safe_relative_path(vault, candidate.target_path)
    except (CanonicalValidationError, Exception) as exc:
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError("candidate target or canonical schema is invalid") from exc
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise PromotionError("canonical target is unsafe")
        if expected_base_hash is None or sha256_file(target) != expected_base_hash:
            raise PromotionError("base hash conflict")
    elif expected_base_hash is not None:
        raise PromotionError("base hash conflict")
    try:
        atomic_write_text(vault, candidate.target_path, candidate.content, expected_hash=expected_base_hash)
    except ConflictError as exc:
        raise PromotionError("base hash conflict") from exc
    _append_action(
        vault,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate.id,
            "target_path": candidate.target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return {"schema_version": "0.1", "status": "promoted", "target_path": candidate.target_path}
