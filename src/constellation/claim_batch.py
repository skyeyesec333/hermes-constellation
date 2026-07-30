"""Safe JSON-file claim staging without shell interpolation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .claim import stage_claim
from .validation import validate_evidence_excerpt


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamps must be ISO-8601 strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed


def _load_items(input_path: Path) -> list[dict[str, object]]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    raw_items = payload.get("claims") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        raise ValueError("input must be a JSON array or an object with a claims array")
    if not raw_items:
        raise ValueError("claims array must not be empty")
    if not all(isinstance(item, dict) for item in raw_items):
        raise ValueError("every claim item must be a JSON object")
    return raw_items


def _stage_one(vault: Path, item: dict[str, object]) -> dict[str, str]:
    allowed = {
        "subject_id", "predicate", "object_id", "object_literal", "source_ids",
        "evidence_anchor", "evidence_excerpt", "claim_status", "confidence",
        "observed_at", "valid_from", "valid_to",
    }
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise ValueError(f"unsupported claim fields: {', '.join(unknown)}")
    subject_id = item.get("subject_id")
    predicate = item.get("predicate")
    source_ids = item.get("source_ids")
    excerpt = item.get("evidence_excerpt")
    if not isinstance(subject_id, str) or not subject_id:
        raise ValueError("subject_id must be a non-empty string")
    if not isinstance(predicate, str) or not predicate:
        raise ValueError("predicate must be a non-empty string")
    if not isinstance(source_ids, list) or not source_ids or not all(
        isinstance(value, str) and value for value in source_ids
    ):
        raise ValueError("source_ids must be a non-empty string array")
    if not isinstance(excerpt, str) or not excerpt:
        raise ValueError("evidence_excerpt must be a non-empty string")
    validate_evidence_excerpt(vault, source_ids, excerpt)
    confidence = item.get("confidence")
    if confidence is not None and not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be numeric")
    raw_object_id = item.get("object_id")
    raw_object_literal = item.get("object_literal")
    raw_evidence_anchor = item.get("evidence_anchor")
    object_id: str | None = raw_object_id if isinstance(raw_object_id, str) else None
    object_literal: str | None = (
        raw_object_literal if isinstance(raw_object_literal, str) else None
    )
    evidence_anchor: str | None = (
        raw_evidence_anchor if isinstance(raw_evidence_anchor, str) else None
    )
    return stage_claim(
        vault,
        subject_id=subject_id,
        predicate=predicate,
        object_id=object_id,
        object_literal=object_literal,
        source_ids=source_ids,
        evidence_anchor=evidence_anchor,
        evidence_excerpt=excerpt,
        claim_status=str(item.get("claim_status") or "source-claimed"),
        confidence=float(confidence) if confidence is not None else None,
        observed_at=_optional_datetime(item.get("observed_at")),
        valid_from=_optional_datetime(item.get("valid_from")),
        valid_to=_optional_datetime(item.get("valid_to")),
    )


def stage_claims_from_file(root: Path | str, input_path: Path | str) -> dict[str, Any]:
    """Stage every valid item and report every failure; never invokes a shell."""
    vault = Path(root).absolute()
    items = _load_items(Path(input_path))
    results: list[dict[str, object]] = []
    for index, item in enumerate(items):
        try:
            staged = _stage_one(vault, item)
            results.append({"index": index, "status": "staged", **staged})
        except Exception as exc:
            results.append(
                {"index": index, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            )
    succeeded = sum(result["status"] == "staged" for result in results)
    failed = len(results) - succeeded
    return {
        "schema_version": "0.1",
        "status": "completed_with_failures" if failed else "completed",
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault")
    parser.add_argument("input_json")
    args = parser.parse_args(argv)
    try:
        result = stage_claims_from_file(args.vault, args.input_json)
    except Exception as exc:
        result = {
            "schema_version": "0.1",
            "status": "failed",
            "succeeded": 0,
            "failed": 1,
            "results": [{"index": None, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}],
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["failed"] else 0