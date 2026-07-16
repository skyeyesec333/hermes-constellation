"""Explicit, conflict-safe review and candidate promotion."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .frontmatter import parse_frontmatter
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


def _ingest_candidate_summary(
    root: Path, path: Path, payload: dict[str, object]
) -> dict[str, object]:
    source_id = payload.get("source_id")
    source_hash = payload.get("source_hash")
    if (
        payload.get("schema_version") != "0.1"
        or payload.get("kind") != "ingest_candidate"
        or payload.get("status") != "pending_review"
        or not isinstance(source_id, str)
        or not isinstance(source_hash, str)
        or path.stem != f"ingest-{source_id}"
    ):
        raise PromotionError("ingest candidate packet is invalid")
    target_relative = f"source-items/{source_id}.md"
    target = safe_relative_path(root, target_relative)
    if not target.is_file() or target.is_symlink():
        raise PromotionError("ingest candidate source-item is missing or unsafe")
    text = target.read_text(encoding="utf-8")
    validate_canonical_text(text, target_relative)
    metadata, _ = parse_frontmatter(text)
    if (
        metadata.get("id") != source_id
        or metadata.get("type") != "source-item"
        or metadata.get("source_hash") != source_hash
    ):
        raise PromotionError("ingest candidate does not match its canonical source-item")
    return {
        "id": path.stem,
        "kind": "ingest_candidate",
        "title": f"Ingest review: {metadata['title']}",
        "target_path": target_relative,
        "expected_base_hash": sha256_file(target),
        "promotable": True,
    }


def list_candidates(root: Path | str) -> list[dict[str, object]]:
    vault = Path(root).absolute()
    results: list[dict[str, object]] = []
    for path in _candidate_files(vault):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            if payload.get("kind") == "ingest_candidate":
                results.append(_ingest_candidate_summary(vault, path, payload))
                continue
            candidate = CandidatePatch.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValidationError, ValueError, PromotionError):
            continue
        results.append(
            {
                "id": candidate.id,
                "kind": "candidate_patch",
                "title": candidate.title,
                "target_path": candidate.target_path,
                "expected_base_hash": candidate.expected_base_hash,
                "promotable": True,
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


def _rebuild_index_after_write(root: Path, result: dict[str, str]) -> dict[str, str]:
    from .retrieval import build_index

    report = build_index(root)
    result["index_generation"] = str(report["generation"])
    return result


def _review_ingest_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _ingest_candidate_summary(root, candidate_path, payload)
    reviewed_hash = summary["expected_base_hash"]
    if expected_base_hash is None or reviewed_hash != expected_base_hash:
        raise PromotionError("base hash conflict")
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if sha256_file(target) != expected_base_hash:
        raise PromotionError("base hash conflict")
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "ingest_candidate_reviewed",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": expected_base_hash,
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "reviewed", "target_path": target_path},
    )


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
    candidate_path = safe_relative_path(
        vault, Path(".constellation/candidates") / f"{candidate_id}.json"
    )
    if not candidate_path.is_file() or candidate_path.is_symlink():
        raise PromotionError("candidate does not exist")
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError("candidate packet is invalid") from exc
    if isinstance(payload, dict) and payload.get("kind") == "ingest_candidate":
        return _review_ingest_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("kind") == "conference-encounter":
        raise PromotionError(
            "conference encounter candidates cannot be auto-promoted — "
            "Aiko hand-writes the entity + outreach draft (see conference-card-intake skill). "
            "The encounter bytes + OCR + PM shell are already staged by lead capture."
        )
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
    return _rebuild_index_after_write(
        vault,
        {"schema_version": "0.1", "status": "promoted", "target_path": candidate.target_path},
    )
