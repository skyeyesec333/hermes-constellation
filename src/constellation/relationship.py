"""Review-gated relationship staging, identity, and supersession
(i2-successor Wave 1 Task 1.3).

Relationships are first-class canonical records, but every mutation stays
review-gated:

- staging writes an envelope candidate packet under
  ``.constellation/candidates/relationship-<ULID>.json`` — never a canonical
  file;
- candidate identity is idempotent per
  ``(assertion_fingerprint, evidence_fingerprint, extractor name/version)``;
  identical re-staging reports ``already_staged`` instead of duplicating;
- promotion lives in ``review.py`` and enforces create-only semantics,
  predicate validation, endpoint existence/visibility, and source resolution;
- supersession marks the old record ``superseded`` (preserved, never deleted)
  and records the typed edge on the replacement, journaled with actor/basis
  and pre-write hashes. Force never writes directly — it stages a review
  candidate.

The assertion fingerprint is a deterministic SHA-256 over normalized
subject, canonical predicate, object, functional interval, and sorted
qualifiers (endpoints sorted first for symmetric predicates). It supports
deduplication, re-review, backfill idempotency, and delta comparison; it
never replaces the stable record ULID.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .frontmatter import parse_frontmatter, render_frontmatter
from .models import CandidatePatch, RelationshipRecord, Sensitivity, generate_ulid
from .predicates import load_predicate_registry, validate_relationship_semantics
from .storage import atomic_write_text, safe_relative_path, sha256_file
from .vault import is_initialized

EXTRACTOR_NAME = "manual-cli"
EXTRACTOR_VERSION = "1"
_TERMINAL_STATES = {"stale", "superseded"}
_ENTITY_FOLDERS = ("entities", "people")
_LEDGER_REL = Path(".constellation/supersedes-ledger.jsonl")


class RelationshipPipelineError(RuntimeError):
    """Raised when relationship staging or supersession fails closed."""


def _load_registry_or_fail():
    try:
        return load_predicate_registry()
    except Exception as exc:
        raise RelationshipPipelineError(f"predicate registry unavailable: {exc}") from exc


def _canonical_predicate(predicate: str, registry) -> tuple[str, str]:
    """Return (canonical_name, resolution_status) for fingerprinting/checks."""
    resolution = registry.resolve(predicate)
    if resolution.status == "unknown":
        return predicate.strip(), "unknown"
    return str(resolution.canonical), resolution.status


def assertion_fingerprint(
    *,
    subject_id: str,
    predicate: str,
    object_id: str,
    valid_from: datetime | str | None = None,
    valid_to: datetime | str | None = None,
    qualifiers: dict[str, str] | None = None,
    registry=None,
) -> str:
    """Deterministic SHA-256 over the normalized assertion identity."""
    active = registry if registry is not None else _load_registry_or_fail()
    canonical, _ = _canonical_predicate(predicate, active)
    subject, obj = str(subject_id), str(object_id)
    entry = active.get(canonical)
    if entry is not None and entry.symmetric and obj < subject:
        subject, obj = obj, subject

    def _iso(value: datetime | str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    payload = {
        "subject_id": subject,
        "predicate": canonical,
        "object_id": obj,
        "valid_from": _iso(valid_from),
        "valid_to": _iso(valid_to),
        "qualifiers": dict(sorted((qualifiers or {}).items())),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _evidence_fingerprint(
    source_ids: list[str],
    evidence_excerpt: str | None,
    evidence_anchor: str | None,
) -> str:
    payload = {
        "source_ids": sorted(str(item) for item in source_ids),
        "evidence_excerpt": evidence_excerpt or "",
        "evidence_anchor": evidence_anchor or "",
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _scan_entities(vault: Path) -> dict[str, dict[str, Any]]:
    """Map entity ULID -> {kind, sensitivity, status} across entity folders."""
    found: dict[str, dict[str, Any]] = {}
    for folder in _ENTITY_FOLDERS:
        base = vault / folder
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            record_id = str(metadata.get("id", ""))
            if record_id and record_id not in found:
                found[record_id] = {
                    "kind": str(metadata.get("type", "")),
                    "sensitivity": str(metadata.get("sensitivity", "internal")),
                    "status": str(metadata.get("status", "")),
                    "folder": folder,
                }
    return found


def _source_ids_resolve(vault: Path, source_ids: list[str]) -> list[str]:
    """Return the subset of source IDs that resolve to canonical source-items."""
    base = vault / "source-items"
    resolved: list[str] = []
    if not base.is_dir():
        return resolved
    existing = {
        path.stem for path in base.glob("*.md") if path.is_file() and not path.is_symlink()
    }
    for source_id in source_ids:
        if str(source_id) in existing:
            resolved.append(str(source_id))
    return resolved


def require_relationship_references(
    vault: Path, record: RelationshipRecord
) -> dict[str, dict[str, Any]]:
    """Fail closed unless endpoints exist and every source resolves.

    Returns the resolved endpoint metadata map for downstream visibility or
    domain/range checks.
    """
    entities = _scan_entities(vault)
    for endpoint in (str(record.subject_id), str(record.object_id)):
        if endpoint not in entities:
            raise RelationshipPipelineError(f"endpoint entity not found: {endpoint}")
    missing_sources = [
        str(item)
        for item in record.source_ids
        if str(item) not in _source_ids_resolve(vault, [str(item)])
    ]
    if missing_sources:
        raise RelationshipPipelineError(
            f"source ids do not resolve to canonical source-items: {missing_sources}"
        )
    return entities


_SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


def validate_promotion_ready(vault: Path, record: RelationshipRecord) -> dict[str, dict[str, Any]]:
    """Fail closed unless endpoints exist, are visible at the record's
    sensitivity, and every source resolves to a canonical source-item."""
    entities = require_relationship_references(vault, record)
    ceiling = _SENSITIVITY_RANK.get(str(record.sensitivity), 1)
    for endpoint in (str(record.subject_id), str(record.object_id)):
        endpoint_rank = _SENSITIVITY_RANK.get(entities[endpoint]["sensitivity"], 3)
        if endpoint_rank > ceiling:
            raise RelationshipPipelineError(
                f"endpoint {endpoint} is not visible at sensitivity {record.sensitivity}"
            )
    return entities


def _candidate_packets(vault: Path) -> list[tuple[Path, dict[str, Any]]]:
    candidates_dir = vault / ".constellation" / "candidates"
    packets: list[tuple[Path, dict[str, Any]]] = []
    if not candidates_dir.is_dir():
        return packets
    for path in sorted(candidates_dir.glob("relationship-*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == "relationship_candidate":
            packets.append((path, payload))
    return packets


def stage_relationship(
    root: Path | str,
    *,
    subject_id: str,
    predicate: str,
    object_id: str,
    source_ids: list[str],
    evidence_class: Literal[
        "verified", "corroborated", "single-source", "inferred", "user-asserted"
    ] = "single-source",
    confidence: float | None = None,
    observed_at: datetime | None = None,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    role: str = "",
    qualifiers: dict[str, str] | None = None,
    evidence_excerpt: str | None = None,
    evidence_anchor: str | None = None,
    experimental: bool = False,
    extractor_name: str = EXTRACTOR_NAME,
    extractor_version: str = EXTRACTOR_VERSION,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> dict[str, Any]:
    """Stage a relationship as a review-only envelope candidate.

    Never writes canonical records. Identical re-staging (same assertion,
    evidence, and extractor identity) reports ``already_staged``. Unknown
    predicates fail unless ``experimental=True``.
    """
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise RelationshipPipelineError("vault is not initialized")
    registry = _load_registry_or_fail()
    canonical, status = _canonical_predicate(predicate, registry)
    if status == "unknown" and not experimental:
        raise RelationshipPipelineError(
            f"predicate {predicate!r} is not in the registry; pass experimental=True to stage "
            "an experimental predicate"
        )

    now = datetime.now(UTC)
    record = RelationshipRecord(
        id=generate_ulid(),
        title=f"relationship-{str(subject_id)[:8]}-{canonical}",
        status="review-required",
        sensitivity=sensitivity,
        subject_id=subject_id,
        predicate=predicate,
        object_id=object_id,
        source_ids=list(source_ids),
        evidence_class=evidence_class,
        confidence=confidence,
        observed_at=observed_at,
        first_seen=first_seen,
        last_seen=last_seen,
        valid_from=valid_from,
        valid_to=valid_to,
        role=role,
        qualifiers=dict(qualifiers or {}),
        created_at=now,
        updated_at=now,
    )
    entities = require_relationship_references(vault, record)
    semantic_findings = validate_relationship_semantics(
        record,
        registry,
        subject_kind=entities[str(record.subject_id)]["kind"],
        object_kind=entities[str(record.object_id)]["kind"],
    )

    fingerprint = assertion_fingerprint(
        subject_id=subject_id,
        predicate=predicate,
        object_id=object_id,
        valid_from=valid_from,
        valid_to=valid_to,
        qualifiers=qualifiers,
        registry=registry,
    )
    evidence_fp = _evidence_fingerprint(list(source_ids), evidence_excerpt, evidence_anchor)

    # Idempotency / revision handling: same assertion fingerprint family.
    for path, packet in _candidate_packets(vault):
        if packet.get("assertion_fingerprint") != fingerprint:
            continue
        same_evidence = (
            packet.get("evidence_fingerprint") == evidence_fp
            and packet.get("extractor", {}).get("name") == extractor_name
            and packet.get("extractor", {}).get("version") == extractor_version
        )
        if same_evidence:
            return {
                "status": "already_staged",
                "candidate_path": path.relative_to(vault).as_posix(),
                "assertion_fingerprint": fingerprint,
            }
        # Changed evidence or extractor: stage a visible revision carrying
        # both extractions and the prior revision pointer. The reviewed
        # packet is never overwritten.
        prior = packet.get("revision", {})
        revision_number = int(prior.get("number", 1)) + 1
        envelope = _envelope(
            record,
            fingerprint=fingerprint,
            evidence_fp=evidence_fp,
            extractor_name=extractor_name,
            extractor_version=extractor_version,
            experimental=experimental,
            evidence_excerpt=evidence_excerpt,
            evidence_anchor=evidence_anchor,
            revision={
                "number": revision_number,
                "prior_revision": path.stem,
                "original_extraction": packet.get("record"),
                "proposed_extraction": json.loads(record.model_dump_json()),
                "last_seen_run": now.isoformat(),
                "reviewer_state": "pending",
            },
        )
        candidate_path = safe_relative_path(
            vault, Path(".constellation/candidates") / f"relationship-{record.id}.json"
        )
        atomic_write_text(
            vault, candidate_path.relative_to(vault), json.dumps(envelope, indent=2) + "\n"
        )
        return {
            "status": "staged_revision",
            "relationship_id": record.id,
            "candidate_path": candidate_path.relative_to(vault).as_posix(),
            "assertion_fingerprint": fingerprint,
            "semantic_findings": [finding.__dict__ for finding in semantic_findings],
        }

    envelope = _envelope(
        record,
        fingerprint=fingerprint,
        evidence_fp=evidence_fp,
        extractor_name=extractor_name,
        extractor_version=extractor_version,
        experimental=experimental,
        evidence_excerpt=evidence_excerpt,
        evidence_anchor=evidence_anchor,
        revision={
            "number": 1,
            "prior_revision": None,
            "original_extraction": None,
            "proposed_extraction": None,
            "last_seen_run": now.isoformat(),
            "reviewer_state": "pending",
        },
    )
    candidate_path = safe_relative_path(
        vault, Path(".constellation/candidates") / f"relationship-{record.id}.json"
    )
    atomic_write_text(
        vault, candidate_path.relative_to(vault), json.dumps(envelope, indent=2) + "\n"
    )
    return {
        "status": "staged",
        "relationship_id": record.id,
        "candidate_path": candidate_path.relative_to(vault).as_posix(),
        "assertion_fingerprint": fingerprint,
        "semantic_findings": [finding.__dict__ for finding in semantic_findings],
    }


def _envelope(
    record: RelationshipRecord,
    *,
    fingerprint: str,
    evidence_fp: str,
    extractor_name: str,
    extractor_version: str,
    experimental: bool,
    evidence_excerpt: str | None,
    evidence_anchor: str | None,
    revision: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "relationship_candidate",
        "schema_version": "0.1",
        "record": json.loads(record.model_dump_json()),
        "assertion_fingerprint": fingerprint,
        "evidence_fingerprint": evidence_fp,
        "extractor": {"name": extractor_name, "version": extractor_version},
        "experimental": experimental,
        "evidence_excerpt": evidence_excerpt,
        "evidence_anchor": evidence_anchor,
        "revision": revision,
    }


def list_staged_relationships(root: Path | str, *, limit: int = 50) -> list[dict[str, Any]]:
    """List staged relationship candidates, bounded and metadata-only."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise RelationshipPipelineError("vault is not initialized")
    bounded = max(1, min(int(limit), 500))
    results: list[dict[str, Any]] = []
    for path, packet in _candidate_packets(vault):
        if len(results) >= bounded:
            break
        record = packet.get("record", {})
        results.append(
            {
                "candidate_id": path.stem,
                "id": record.get("id"),
                "title": record.get("title"),
                "subject_id": record.get("subject_id"),
                "predicate": record.get("predicate"),
                "object_id": record.get("object_id"),
                "assertion_fingerprint": packet.get("assertion_fingerprint"),
                "revision": packet.get("revision", {}).get("number", 1),
                "experimental": bool(packet.get("experimental", False)),
            }
        )
    return results


def _load_relationship(vault: Path, record_id: str) -> tuple[dict[str, Any], str, Path]:
    path = vault / "relationships" / f"{record_id}.md"
    if path.is_symlink() or not path.is_file():
        raise RelationshipPipelineError(f"relationship not found: {record_id}")
    try:
        metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RelationshipPipelineError(f"relationship {record_id} is not parseable: {exc}") from exc
    if str(metadata.get("id", "")) != record_id:
        raise RelationshipPipelineError(
            f"relationship id mismatch: path says {record_id}, frontmatter says "
            f"{metadata.get('id')!r}"
        )
    return metadata, body, path


def _summary(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(metadata.get("id", "")),
        "title": str(metadata.get("title", "")),
        "status": str(metadata.get("status", "")),
        "predicate": str(metadata.get("predicate", "")),
        "subject_id": str(metadata.get("subject_id", "")),
        "object_id": str(metadata.get("object_id", "")),
        "supersedes": [str(item) for item in (metadata.get("supersedes") or [])],
        "path": f"relationships/{metadata.get('id', '')}.md",
    }


def _append_ledger(vault: Path, entry: dict[str, Any]) -> None:
    ledger = vault / _LEDGER_REL
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def supersede_relationship(
    root: Path | str,
    *,
    new_id: str,
    old_id: str,
    actor: str,
    basis: list[str],
    force: bool = False,
) -> dict[str, Any]:
    """Mark old_id superseded and record the typed edge on new_id.

    Review-gated correction path: the old record is preserved with status
    ``superseded``; the replacement carries ``supersedes: [old_id]``; every
    application is journaled. Reruns are idempotent. Superseding an
    already-terminal record is refused unless ``force=True``, and force
    stages a review candidate instead of writing directly.
    """
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise RelationshipPipelineError("vault is not initialized")
    if new_id == old_id:
        raise RelationshipPipelineError("a relationship cannot supersede itself")
    if not actor.strip():
        raise RelationshipPipelineError("actor is required (who asserts this supersede)")
    if not basis:
        raise RelationshipPipelineError("basis is required (source ULIDs or review id)")

    old_meta, old_body, old_path = _load_relationship(vault, old_id)
    new_meta, new_body, new_path = _load_relationship(vault, new_id)

    old_status = str(old_meta.get("status", "active"))
    new_links = [str(item) for item in (new_meta.get("supersedes") or [])]

    if old_status in _TERMINAL_STATES and old_id in new_links:
        return {
            "status": "already_applied",
            "old_relationship": _summary(old_meta),
            "new_relationship": _summary(new_meta),
        }

    if old_status in _TERMINAL_STATES:
        if not force:
            raise RelationshipPipelineError(
                f"relationship {old_id} is already {old_status}; rerun with force "
                "to stage a review candidate instead of a direct write"
            )
        updated_new = dict(new_meta)
        updated_new["supersedes"] = new_links + [old_id]
        updated_new["updated_at"] = datetime.now(UTC).isoformat()
        candidate = CandidatePatch(
            type="candidate_patch",
            title=f"Supersede already-{old_status} relationship {old_id} by {new_id}",
            status="review-required",
            sensitivity=Sensitivity(str(new_meta.get("sensitivity", "internal"))),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            target_path=f"relationships/{new_id}.md",
            content=render_frontmatter(updated_new, new_body),
            expected_base_hash=sha256_file(new_path),
        )
        from .review import write_candidate

        candidate_path = write_candidate(vault, candidate)
        return {
            "status": "staged_review",
            "candidate_id": candidate.id,
            "candidate_path": candidate_path.as_posix(),
            "old_relationship": _summary(old_meta),
            "new_relationship": _summary(new_meta),
        }

    old_hash = sha256_file(old_path)
    new_hash = sha256_file(new_path)
    now = datetime.now(UTC).isoformat()

    updated_old = dict(old_meta)
    updated_old["status"] = "superseded"
    updated_old["updated_at"] = now
    atomic_write_text(
        vault,
        old_path.relative_to(vault),
        render_frontmatter(updated_old, old_body),
        expected_hash=old_hash,
    )

    updated_new = dict(new_meta)
    updated_new["supersedes"] = new_links + [old_id]
    updated_new["updated_at"] = now
    atomic_write_text(
        vault,
        new_path.relative_to(vault),
        render_frontmatter(updated_new, new_body),
        expected_hash=new_hash,
    )

    _append_ledger(
        vault,
        {
            "event": "relationship_supersede",
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "basis": [str(item) for item in basis],
            "new_relationship_id": new_id,
            "old_relationship_id": old_id,
            "old_relationship_hash": old_hash,
            "new_relationship_hash": new_hash,
            "prior_old_status": old_status,
        },
    )

    return {
        "status": "applied",
        "old_relationship": _summary(updated_old),
        "new_relationship": _summary(updated_new),
        "ledger": _LEDGER_REL.as_posix(),
    }
