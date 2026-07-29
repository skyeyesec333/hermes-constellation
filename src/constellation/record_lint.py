"""Record-health linting — read-only integrity findings over canonical records.

Every finding links to the exact record, carries a severity, and proposes a
reviewable next action. This module never mutates canonical records.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .vault import is_initialized


class RecordLintError(RuntimeError):
    """Raised when linting cannot proceed safely."""


def _scan(vault: Path, folder: str) -> list[tuple[str, dict[str, Any]]]:
    base = vault / folder
    if not base.is_dir():
        return []
    records: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if metadata.get("id"):
            records.append((f"{folder}/{path.name}", metadata))
    return records


def lint_records(vault: Path | str) -> dict[str, Any]:
    """Lint canonical records and return cited, severity-ranked findings.

    Read-only: no record is created, modified, or deleted.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise RecordLintError("vault is not initialized")

    findings: list[dict[str, Any]] = []

    claims = _scan(vault, "claims")
    source_ids = {str(m["id"]) for _, m in _scan(vault, "source-items")}
    entity_ids = {str(m["id"]) for _, m in _scan(vault, "entities")}
    entity_ids |= {str(m["id"]) for _, m in _scan(vault, "people")}

    for record_path, metadata in claims:
        claim_id = str(metadata["id"])
        sources = [str(s) for s in metadata.get("source_ids", [])]
        if not sources:
            findings.append({
                "check": "claim_without_sources",
                "severity": "high",
                "record_id": claim_id,
                "record_path": record_path,
                "detail": f"claim '{metadata.get('title', claim_id)}' has no source anchors",
                "suggested_action": "attach source_ids via candidate patch or retract the claim",
            })
        for source_id in sources:
            if source_id not in source_ids:
                findings.append({
                    "check": "broken_source_reference",
                    "severity": "high",
                    "record_id": claim_id,
                    "record_path": record_path,
                    "detail": f"claim references missing source-item {source_id}",
                    "suggested_action": "restore the source-item or re-anchor the claim",
                })
        subject = str(metadata.get("subject_id", ""))
        if subject and subject not in entity_ids:
            findings.append({
                "check": "broken_subject_reference",
                "severity": "high",
                "record_id": claim_id,
                "record_path": record_path,
                "detail": f"claim subject {subject} has no canonical entity/person record",
                "suggested_action": "create the entity or repoint the claim",
            })

    # Contradictory active claims: same subject+predicate, differing object,
    # both active, no visible resolution state.
    by_key: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for record_path, metadata in claims:
        if metadata.get("status") != "active":
            continue
        obj = metadata.get("object_id") or metadata.get("object_literal")
        if not obj:
            continue
        key = (str(metadata.get("subject_id", "")), str(metadata.get("predicate", "")))
        by_key[key].append((str(metadata["id"]), str(obj), record_path))
    for (subject, predicate), entries in sorted(by_key.items()):
        objects = {obj for _, obj, _ in entries}
        if len(objects) > 1:
            findings.append({
                "check": "contradictory_claims",
                "severity": "medium",
                "record_id": entries[0][0],
                "record_path": entries[0][2],
                "detail": (
                    f"{len(entries)} active claims on {subject} predicate "
                    f"'{predicate}' disagree: {sorted(objects)}"
                ),
                "suggested_action": "review and mark one claim superseded or retracted",
            })

    # Review backlog: pending-review candidates older than 7 days.
    candidates_dir = vault / ".constellation/candidates"
    if candidates_dir.is_dir():
        import json
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        for path in sorted(candidates_dir.glob("*.json")):
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if candidate.get("status") != "pending-review":
                continue
            created = candidate.get("created_at", "")
            try:
                age_days = (now - datetime.fromisoformat(str(created).replace("Z", "+00:00"))).days
            except ValueError:
                continue
            if age_days >= 7:
                findings.append({
                    "check": "stale_pending_candidate",
                    "severity": "medium",
                    "record_id": str(candidate.get("id", path.stem)),
                    "record_path": f".constellation/candidates/{path.name}",
                    "detail": f"candidate pending review for {age_days} days",
                    "suggested_action": "promote, archive, or reject with an explicit decision",
                })

    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (severity_order.get(str(f["severity"]), 3), str(f["check"]), str(f["record_id"])))
    summary: dict[str, int] = {"total": len(findings), "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        severity = str(finding["severity"])
        summary[severity] = summary.get(severity, 0) + 1
    return {"findings": findings, "summary": summary}
