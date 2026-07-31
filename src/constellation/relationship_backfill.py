"""Conservative relationship backfill planner (i2-successor Wave 2 Task 2.4).

Turns existing canonical entity-to-entity claims into review-gated
relationship candidates — never direct writes, never bulk promotion.

Eligible sources, in order:

1. canonical entity-to-entity claims with explicit ``object_id``, source
   IDs, and a registry-recognized predicate;
2. legacy structured fields resolving both endpoints and evidence (none
   exist in the current schema; the lane is reported, not used);
3. never plain body co-occurrence.

Proposals are deduplicated by canonicalized assertion fingerprint
(subject, canonical predicate, object, validity interval) with evidence
preserved as merged source IDs. When an equivalent canonical relationship
exists, the proposal is a corroboration candidate patch on that record —
never a duplicate. Plans carry the vault inventory hash and staging refuses
a stale plan. Everything is deterministic and bounded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter, render_frontmatter
from .predicates import default_registry
from .relationship import assertion_fingerprint, stage_relationship
from .review import write_candidate
from .storage import safe_relative_path, sha256_file
from .vault import is_initialized

PLAN_VERSION = 1
_EXTRACTOR_NAME = "relationship-backfill"
_SCAN_FOLDERS = ("claims", "relationships", "entities", "people")


class BackfillError(RuntimeError):
    """Raised when backfill planning or staging fails closed."""


def _scan_metadata(vault: Path, folder: str) -> list[dict[str, Any]]:
    base = vault / folder
    records: list[dict[str, Any]] = []
    if not base.is_dir():
        return records
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if metadata.get("id"):
            records.append(metadata)
    return records


def vault_inventory_hash(vault: Path) -> str:
    """Deterministic hash over the canonical folders backfill reads."""
    digest = hashlib.sha256()
    for folder in _SCAN_FOLDERS:
        base = vault / folder
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            digest.update(path.relative_to(vault).as_posix().encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
            digest.update(b"\n")
    return digest.hexdigest()


def _endpoint_ids(vault: Path) -> set[str]:
    ids: set[str] = set()
    for folder in ("entities", "people"):
        for record in _scan_metadata(vault, folder):
            ids.add(str(record["id"]))
    return ids


def _source_ids(vault: Path) -> set[str]:
    return {str(record["id"]) for record in _scan_metadata(vault, "source-items")}


def _eligible_claims(vault: Path, registry) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return (eligible claim metadata, ineligible counters)."""
    ineligible = {"unrecognized_predicate": 0, "literal_only": 0, "missing_source_ids": 0}
    eligible: list[dict[str, Any]] = []
    for record in _scan_metadata(vault, "claims"):
        if not record.get("object_id"):
            ineligible["literal_only"] += 1
            continue
        if not record.get("source_ids"):
            ineligible["missing_source_ids"] += 1
            continue
        resolution = registry.resolve(str(record.get("predicate", ""))) if registry else None
        if resolution is None or resolution.status == "unknown":
            ineligible["unrecognized_predicate"] += 1
            continue
        record["_canonical_predicate"] = str(resolution.canonical)
        eligible.append(record)
    return eligible, ineligible


def _claim_fingerprint(record: dict[str, Any], registry) -> str:
    return assertion_fingerprint(
        subject_id=str(record["subject_id"]),
        predicate=str(record["_canonical_predicate"]),
        object_id=str(record["object_id"]),
        valid_from=record.get("valid_from"),
        valid_to=record.get("valid_to"),
        registry=registry,
    )


def _relationship_fingerprints(vault: Path, registry) -> dict[str, dict[str, Any]]:
    """Map assertion fingerprint -> canonical relationship record."""
    found: dict[str, dict[str, Any]] = {}
    for record in _scan_metadata(vault, "relationships"):
        fingerprint = assertion_fingerprint(
            subject_id=str(record.get("subject_id", "")),
            predicate=str(record.get("predicate", "")),
            object_id=str(record.get("object_id", "")),
            valid_from=record.get("valid_from"),
            valid_to=record.get("valid_to"),
            qualifiers={
                str(k): str(v) for k, v in (record.get("qualifiers") or {}).items()
            },
            registry=registry,
        )
        found[fingerprint] = record
    return found


def backfill_inventory(root: Path | str) -> dict[str, Any]:
    """Read-only eligibility inventory. Metadata counts only."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise BackfillError("vault is not initialized")
    registry = default_registry()
    eligible, ineligible = _eligible_claims(vault, registry)
    claims_e2e = sum(1 for r in _scan_metadata(vault, "claims") if r.get("object_id"))
    return {
        "status": "ok",
        "claims_entity_to_entity": claims_e2e,
        "eligible": len(eligible),
        "ineligible": ineligible,
        "canonical_relationships": len(_scan_metadata(vault, "relationships")),
        "legacy_structured_fields": 0,
        "vault_inventory_hash": vault_inventory_hash(vault),
    }


def backfill_plan(root: Path | str, out: Path | str) -> dict[str, Any]:
    """Write a deterministic, staleness-guarded staging plan. No vault writes."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise BackfillError("vault is not initialized")
    registry = default_registry()
    if registry is None:
        raise BackfillError("predicate registry unavailable")
    eligible, ineligible = _eligible_claims(vault, registry)
    endpoints = _endpoint_ids(vault)
    sources = _source_ids(vault)
    existing = _relationship_fingerprints(vault, registry)

    groups: dict[str, list[dict[str, Any]]] = {}
    unresolved: list[dict[str, Any]] = []
    for record in eligible:
        fingerprint = _claim_fingerprint(record, registry)
        if str(record["subject_id"]) not in endpoints or str(record["object_id"]) not in endpoints:
            unresolved.append({"claim_id": str(record["id"]), "reason": "unresolved_endpoint"})
            continue
        missing = [str(s) for s in record["source_ids"] if str(s) not in sources]
        if missing:
            unresolved.append({"claim_id": str(record["id"]), "reason": "unresolved_source"})
            continue
        groups.setdefault(fingerprint, []).append(record)

    proposals: list[dict[str, Any]] = []
    for fingerprint in sorted(groups):
        members = groups[fingerprint]
        first = members[0]
        source_ids = sorted({str(s) for r in members for s in r["source_ids"]})
        claim_ids = sorted(str(r["id"]) for r in members)
        canonical = existing.get(fingerprint)
        proposal: dict[str, Any] = {
            "assertion_fingerprint": fingerprint,
            "subject_id": str(first["subject_id"]),
            "predicate": str(first["_canonical_predicate"]),
            "object_id": str(first["object_id"]),
            "valid_from": str(first["valid_from"]) if first.get("valid_from") else None,
            "valid_to": str(first["valid_to"]) if first.get("valid_to") else None,
            "source_ids": source_ids,
            "claim_ids": claim_ids,
        }
        if canonical is not None:
            proposal["action"] = "corroborate"
            proposal["existing_relationship_id"] = str(canonical["id"])
            proposal["existing_source_ids"] = sorted(
                str(s) for s in (canonical.get("source_ids") or [])
            )
        else:
            proposal["action"] = "create"
        proposals.append(proposal)

    plan = {
        "version": PLAN_VERSION,
        "vault_inventory_hash": vault_inventory_hash(vault),
        "proposals": proposals,
        "unresolved": sorted(unresolved, key=lambda item: item["claim_id"]),
        "ineligible": ineligible,
        "extractor": {"name": _EXTRACTOR_NAME, "version": str(PLAN_VERSION)},
    }
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "ok",
        "plan_path": str(out_path),
        "proposals": len(proposals),
        "unresolved": len(unresolved),
        "vault_inventory_hash": plan["vault_inventory_hash"],
    }


def _pending_patch_targets(vault: Path) -> set[str]:
    candidates_dir = vault / ".constellation" / "candidates"
    targets: set[str] = set()
    if not candidates_dir.is_dir():
        return targets
    for path in sorted(candidates_dir.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("type") == "candidate_patch":
            targets.add(str(payload.get("target_path", "")))
    return targets


def backfill_stage(root: Path | str, plan_file: Path | str, *, limit: int = 50) -> dict[str, Any]:
    """Stage plan proposals as review candidates. Bounded, idempotent,
    never a canonical write. Refuses a stale plan."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise BackfillError("vault is not initialized")
    if not 1 <= limit <= 500:
        raise BackfillError("limit must be between 1 and 500")
    plan_path = Path(plan_file)
    if not plan_path.is_file():
        raise BackfillError(f"plan not found: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BackfillError("plan is not valid JSON") from exc
    if not isinstance(plan, dict) or plan.get("version") != PLAN_VERSION:
        raise BackfillError("unsupported plan version")
    current_hash = vault_inventory_hash(vault)
    if plan.get("vault_inventory_hash") != current_hash:
        raise BackfillError(
            "plan is stale: vault inventory hash changed; regenerate the plan"
        )

    staged = 0
    already_staged = 0
    corroborations = 0
    corroborations_existing = 0
    errors: list[dict[str, str]] = []
    patch_targets = _pending_patch_targets(vault)
    processed = 0
    for proposal in plan.get("proposals", []):
        if processed >= limit:
            break
        processed += 1
        if proposal["action"] == "create":
            valid_from = (
                datetime.fromisoformat(str(proposal["valid_from"]).replace("Z", "+00:00"))
                if proposal.get("valid_from")
                else None
            )
            valid_to = (
                datetime.fromisoformat(str(proposal["valid_to"]).replace("Z", "+00:00"))
                if proposal.get("valid_to")
                else None
            )
            result = stage_relationship(
                vault,
                subject_id=str(proposal["subject_id"]),
                predicate=str(proposal["predicate"]),
                object_id=str(proposal["object_id"]),
                source_ids=[str(s) for s in proposal["source_ids"]],
                valid_from=valid_from,
                valid_to=valid_to,
                extractor_name=_EXTRACTOR_NAME,
                extractor_version=str(PLAN_VERSION),
            )
            if result["status"] == "already_staged":
                already_staged += 1
            else:
                staged += 1
            continue
        # corroborate: review candidate patch adding merged source IDs
        relationship_id = str(proposal["existing_relationship_id"])
        target_rel = f"relationships/{relationship_id}.md"
        if target_rel in patch_targets:
            corroborations_existing += 1
            continue
        target = safe_relative_path(vault, target_rel)
        if not target.is_file() or target.is_symlink():
            errors.append({"proposal": proposal["assertion_fingerprint"], "error": "relationship_missing"})
            continue
        metadata, body = parse_frontmatter(target.read_text(encoding="utf-8"))
        raw_sources = metadata.get("source_ids") or []
        existing_sources = [str(s) for s in (raw_sources if isinstance(raw_sources, list) else [])]
        merged = sorted(set(existing_sources) | {str(s) for s in proposal["source_ids"]})
        if merged == sorted(existing_sources):
            corroborations_existing += 1
            continue
        updated = dict(metadata)
        updated["source_ids"] = merged
        updated["updated_at"] = datetime.now(UTC).isoformat()
        from .models import CandidatePatch, Sensitivity

        candidate = CandidatePatch(
            type="candidate_patch",
            title=f"Backfill corroboration: add {len(proposal['source_ids'])} source(s) to {relationship_id}",
            status="review-required",
            sensitivity=Sensitivity(str(metadata.get("sensitivity", "internal"))),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            target_path=target_rel,
            content=render_frontmatter(updated, body),
            expected_base_hash=sha256_file(target),
        )
        write_candidate(vault, candidate)
        patch_targets.add(target_rel)
        corroborations += 1

    return {
        "status": "ok",
        "processed": processed,
        "staged": staged,
        "already_staged": already_staged,
        "corroborations_staged": corroborations,
        "corroborations_already_present": corroborations_existing,
        "errors": errors,
        "limit": limit,
    }
