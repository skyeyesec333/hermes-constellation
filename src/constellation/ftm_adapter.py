"""Bounded FollowTheMoney exchange adapter (i2-successor Wave 2 Task 2.1).

File-only import/export of a useful FtM subset, independently implemented
against the behavior contract in docs/graph-intelligence.md. No FtM code or
data is copied; schema/property names are mapped under MIT compatibility.

Import contract:

- bounded by byte/entity/relationship limits from the mapping profile;
- ``--dry-run`` (the default) writes nothing;
- ``--stage`` writes only review candidates — entities as create-only
  CandidatePatch packets, relationships through the review-gated
  ``stage_relationship`` path;
- unresolved proof never becomes a fake source ULID — the relationship is
  reported blocked with a reason;
- stable external FtM IDs are preserved through ``external_ids["ftm"]``;
- re-import is idempotent per external ID (already_canonical /
  already_staged, never duplicated).

Export contract: canonical records only (never candidates), sensitivity
ceiling enforced, unmapped predicates excluded and counted, deterministic
byte-stable NDJSON.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .frontmatter import parse_frontmatter, render_frontmatter
from .models import CandidatePatch, EntityKind, EntityRecord, Sensitivity, generate_ulid
from .relationship import stage_relationship
from .vault import is_initialized

_EXTRACTOR_NAME = "ftm-import"
_SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
_KIND_TO_FTM = {"person": "Person", "company": "Company", "organization": "Organization"}


class FtmAdapterError(RuntimeError):
    """Raised when FtM exchange fails closed."""


def _default_profile_path() -> Path:
    return Path(__file__).resolve().parents[2] / "resources" / "mappings" / "followthemoney-v1.yaml"


def load_ftm_profile(path: Path | None = None) -> dict[str, Any]:
    profile_path = Path(path) if path is not None else _default_profile_path()
    if not profile_path.is_file():
        raise FtmAdapterError(f"FtM mapping profile not found: {profile_path}")
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or int(raw.get("version", 0)) != 1:
        raise FtmAdapterError("unsupported FtM mapping profile")
    for key in ("limits", "entity_schemata", "relationship_schemata", "export_predicates"):
        if key not in raw:
            raise FtmAdapterError(f"FtM mapping profile missing section: {key}")
    return raw


def parse_ftm_ndjson(path: Path | str, *, profile: dict[str, Any]) -> dict[str, Any]:
    """Parse and bound an FtM NDJSON file. Raises on limit breaches."""
    source = Path(path)
    if not source.is_file():
        raise FtmAdapterError(f"FtM input not found: {source}")
    data = source.read_bytes()
    limits = profile["limits"]
    if len(data) > int(limits["max_bytes"]):
        raise FtmAdapterError("FtM input exceeds the byte limit")
    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    skipped_invalid = 0
    by_schema: dict[str, int] = {}
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped_invalid += 1
            continue
        if not isinstance(record, dict) or not record.get("id") or not record.get("schema"):
            skipped_invalid += 1
            continue
        schema = str(record["schema"])
        by_schema[schema] = by_schema.get(schema, 0) + 1
        if schema in profile["entity_schemata"]:
            entities.append(record)
        elif schema in profile["relationship_schemata"]:
            relationships.append(record)
        else:
            skipped_invalid += 1
    if len(entities) > int(limits["max_entities"]):
        raise FtmAdapterError("FtM input exceeds the entity limit")
    if len(relationships) > int(limits["max_relationships"]):
        raise FtmAdapterError("FtM input exceeds the relationship limit")
    return {
        "entities": entities,
        "relationships": relationships,
        "entity_count": len(entities),
        "relationship_count": len(relationships),
        "skipped_invalid": skipped_invalid,
        "by_schema": dict(sorted(by_schema.items())),
        "input_sha256": hashlib.sha256(data).hexdigest(),
    }


def _first(record: dict[str, Any], prop: str) -> str | None:
    values = record.get("properties", {}).get(prop)
    if isinstance(values, list) and values:
        return str(values[0])
    return None


def _scan_canonical_entities(vault: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return (ftm_id -> ULID, ULID -> folder) across entity folders."""
    by_ftm: dict[str, str] = {}
    folders: dict[str, str] = {}
    for folder in ("entities", "people"):
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
            if not record_id:
                continue
            folders[record_id] = folder
            ftm_id = (metadata.get("external_ids") or {}).get("ftm")
            if ftm_id:
                by_ftm[str(ftm_id)] = record_id
    return by_ftm, folders


def _pending_entity_candidates(vault: Path) -> dict[str, str]:
    """Map external FtM ID -> pending candidate ID for entity patches."""
    candidates_dir = vault / ".constellation" / "candidates"
    pending: dict[str, str] = {}
    if not candidates_dir.is_dir():
        return pending
    for path in sorted(candidates_dir.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("type") != "candidate_patch":
            continue
        try:
            metadata, _ = parse_frontmatter(str(payload.get("content", "")))
        except Exception:
            continue
        ftm_id = (metadata.get("external_ids") or {}).get("ftm")
        if ftm_id:
            pending[str(ftm_id)] = str(payload.get("id"))
    return pending


def _source_ids_by_url(vault: Path) -> dict[str, str]:
    base = vault / "source-items"
    resolved: dict[str, str] = {}
    if not base.is_dir():
        return resolved
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        url = metadata.get("source_url")
        record_id = str(metadata.get("id", ""))
        if url and record_id:
            resolved[str(url)] = record_id
    return resolved


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError as exc:
        raise FtmAdapterError(f"invalid FtM date: {value!r}") from exc


def ftm_import(
    root: Path | str,
    file: Path | str,
    *,
    stage: bool = False,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bounded FtM import. Dry-run by default; staging writes candidates only."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise FtmAdapterError("vault is not initialized")
    active_profile = profile if profile is not None else load_ftm_profile()
    document = parse_ftm_ndjson(file, profile=active_profile)

    canonical_by_ftm, _ = _scan_canonical_entities(vault)
    pending = _pending_entity_candidates(vault)

    entity_report = {
        "total": document["entity_count"],
        "by_schema": {
            schema: count
            for schema, count in document["by_schema"].items()
            if schema in active_profile["entity_schemata"]
        },
        "already_canonical": 0,
        "staged": 0,
        "already_staged": 0,
        "skipped": 0,
    }
    staged_candidates: list[str] = []
    if stage:
        for record in document["entities"]:
            ftm_id = str(record["id"])
            if ftm_id in canonical_by_ftm:
                entity_report["already_canonical"] += 1
                continue
            if ftm_id in pending:
                entity_report["already_staged"] += 1
                continue
            name = _first(record, "name")
            mapping = active_profile["entity_schemata"][str(record["schema"])]
            if not name:
                entity_report["skipped"] += 1
                continue
            now = datetime.now(UTC)
            entity = EntityRecord(
                id=generate_ulid(),
                type=EntityKind(mapping["kind"]),
                title=name,
                status="active",
                sensitivity=Sensitivity.INTERNAL,
                source_ids=[],
                external_ids={"ftm": ftm_id},
                created_at=now,
                updated_at=now,
            )
            content = render_frontmatter(
                entity.model_dump(mode="json", exclude_none=True), f"# {entity.title}\n"
            )
            candidate = CandidatePatch(
                type="candidate_patch",
                title=f"Import FtM {record['schema']}: {name}",
                status="review-required",
                sensitivity=Sensitivity.INTERNAL,
                created_at=now,
                updated_at=now,
                target_path=f"{mapping['folder']}/{entity.id}.md",
                content=content,
                expected_base_hash=None,
            )
            from .review import write_candidate

            candidate_path = write_candidate(vault, candidate)
            staged_candidates.append(candidate_path.as_posix())
            pending[ftm_id] = candidate.id
            entity_report["staged"] += 1

    relationship_report: dict[str, Any] = {
        "total": document["relationship_count"], "staged": 0, "blocked": []
    }
    sources_by_url = _source_ids_by_url(vault)
    if stage:
        for record in document["relationships"]:
            ftm_id = str(record["id"])
            mapping = active_profile["relationship_schemata"][str(record["schema"])]
            subject_ftm = _first(record, mapping["subject_property"])
            object_ftm = _first(record, mapping["object_property"])
            blocked = relationship_report["blocked"]
            if subject_ftm and object_ftm and subject_ftm == object_ftm:
                blocked.append({"id": ftm_id, "reason": "self_relationship"})
                continue
            subject_id = canonical_by_ftm.get(str(subject_ftm))
            object_id = canonical_by_ftm.get(str(object_ftm))
            if subject_id is None or object_id is None:
                blocked.append({"id": ftm_id, "reason": "endpoint_not_canonical"})
                continue
            proof = _first(record, "proof")
            source_id = sources_by_url.get(str(proof)) if proof else None
            if source_id is None:
                blocked.append({"id": ftm_id, "reason": "unresolved_proof"})
                continue
            try:
                valid_from = _parse_date(_first(record, "startDate"))
                valid_to = _parse_date(_first(record, "endDate"))
            except FtmAdapterError:
                blocked.append({"id": ftm_id, "reason": "invalid_temporal"})
                continue
            qualifiers: dict[str, str] = {}
            if mapping.get("percentage_property"):
                percentage = _first(record, str(mapping["percentage_property"]))
                if percentage:
                    qualifiers["percentage"] = percentage
            role = _first(record, str(mapping.get("role_property") or "role")) or ""
            result = stage_relationship(
                vault,
                subject_id=subject_id,
                predicate=str(mapping["predicate"]),
                object_id=object_id,
                source_ids=[source_id],
                valid_from=valid_from,
                valid_to=valid_to,
                role=role,
                qualifiers=qualifiers,
                extractor_name=_EXTRACTOR_NAME,
                extractor_version=str(active_profile["version"]),
            )
            if result["status"] in {"staged", "staged_revision"}:
                relationship_report["staged"] += 1
    relationship_report["blocked"] = sorted(
        relationship_report["blocked"], key=lambda item: str(item["id"])
    )

    return {
        "status": "ok",
        "mode": "stage" if stage else "dry-run",
        "input_sha256": document["input_sha256"],
        "skipped_invalid": document["skipped_invalid"],
        "entities": entity_report,
        "relationships": relationship_report,
        "staged_candidates": staged_candidates,
        "limits": dict(active_profile["limits"]),
    }


def ftm_export(
    root: Path | str,
    out: Path | str,
    *,
    sensitivity: str = "internal",
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export the canonical, sensitivity-bounded subset as FtM NDJSON."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise FtmAdapterError("vault is not initialized")
    ceiling = _SENSITIVITY_RANK.get(sensitivity)
    if ceiling is None:
        raise FtmAdapterError(f"unknown sensitivity ceiling: {sensitivity}")
    active_profile = profile if profile is not None else load_ftm_profile()

    entity_report = {"exported": 0, "excluded_by_sensitivity": 0, "excluded_unmapped": 0}
    relationship_report = {
        "exported": 0, "excluded_by_sensitivity": 0, "excluded_unmapped": 0,
        "excluded_unresolved_endpoint": 0,
    }
    lines: list[dict[str, Any]] = []
    exported_ids: dict[str, str] = {}  # ULID -> external id

    for folder in ("entities", "people"):
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
            if _SENSITIVITY_RANK.get(str(metadata.get("sensitivity", "internal")), 3) > ceiling:
                entity_report["excluded_by_sensitivity"] += 1
                continue
            kind = str(metadata.get("type", ""))
            schema = _KIND_TO_FTM.get(kind)
            if schema is None:
                entity_report["excluded_unmapped"] += 1
                continue
            record_id = str(metadata.get("id", ""))
            external = str((metadata.get("external_ids") or {}).get("ftm") or f"constellation-{record_id}")
            exported_ids[record_id] = external
            properties: dict[str, list[str]] = {"name": [str(metadata.get("title", record_id))]}
            aliases = [str(item) for item in (metadata.get("aliases") or [])]
            if aliases:
                properties["alias"] = aliases
            lines.append({"id": external, "schema": schema, "properties": properties})
            entity_report["exported"] += 1

    relationships_dir = vault / "relationships"
    if relationships_dir.is_dir():
        for path in sorted(relationships_dir.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if _SENSITIVITY_RANK.get(str(metadata.get("sensitivity", "internal")), 3) > ceiling:
                relationship_report["excluded_by_sensitivity"] += 1
                continue
            predicate = str(metadata.get("predicate", ""))
            schema = active_profile["export_predicates"].get(predicate)
            if schema is None:
                relationship_report["excluded_unmapped"] += 1
                continue
            subject = str(metadata.get("subject_id", ""))
            obj = str(metadata.get("object_id", ""))
            if subject not in exported_ids or obj not in exported_ids:
                relationship_report["excluded_unresolved_endpoint"] += 1
                continue
            mapping = active_profile["relationship_schemata"][schema]
            properties = {
                str(mapping["subject_property"]): [exported_ids[subject]],
                str(mapping["object_property"]): [exported_ids[obj]],
            }
            if metadata.get("valid_from"):
                properties["startDate"] = [str(metadata["valid_from"])[:10]]
            if metadata.get("valid_to"):
                properties["endDate"] = [str(metadata["valid_to"])[:10]]
            if metadata.get("role"):
                properties[str(mapping.get("role_property") or "role")] = [str(metadata["role"])]
            qualifiers = metadata.get("qualifiers") or {}
            if mapping.get("percentage_property") and qualifiers.get("percentage"):
                properties[str(mapping["percentage_property"])] = [str(qualifiers["percentage"])]
            lines.append({
                "id": f"constellation-{metadata.get('id', '')}",
                "schema": schema,
                "properties": properties,
            })
            relationship_report["exported"] += 1

    lines.sort(key=lambda line: (str(line["schema"]), str(line["id"])))
    text = "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines)
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return {
        "status": "ok",
        "output_path": str(out_path),
        "bytes_written": len(text.encode("utf-8")),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "sensitivity": sensitivity,
        "entities": entity_report,
        "relationships": relationship_report,
    }
