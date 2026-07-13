"""Read-only inventory and dry-run planning for legacy vault migration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .frontmatter import FrontmatterError, parse_frontmatter, render_frontmatter
from .validation import (
    ALLOWED_CANONICAL_FOLDERS,
    CanonicalValidationError,
    validate_canonical_text,
)

MAX_MARKDOWN_BYTES = 10 * 1024 * 1024
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_ENTITY_FOLDERS = {
    "companies": "company",
    "entities": None,
    "organizations": "organization",
    "people": "person",
}
_SENSITIVITY_MAP = {
    "public": "public",
    "internal": "internal",
    "normal": "internal",
    "low": "internal",
    "confidential": "confidential",
    "restricted": "restricted",
}
_INTERNAL_ROOTS = {".constellation"}
_OPERATIONAL_ROOTS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}
_OPERATIONAL_FILES = {".ds_store"}


class MigrationError(RuntimeError):
    """Raised when a vault cannot be inventoried safely."""


_AUTO_DISCOVERY_MARKER = "--- auto-discovered degree-2 skeleton below confidence threshold"


def _load_legacy_mapping(raw: str) -> dict[str, object]:
    try:
        metadata = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrontmatterError("frontmatter is invalid YAML") from exc
    if not isinstance(metadata, dict) or not all(isinstance(key, str) for key in metadata):
        raise FrontmatterError("frontmatter must be a string-keyed mapping")
    return metadata


def parse_legacy_frontmatter(
    text: str,
) -> tuple[dict[str, object], str, dict[str, object] | None]:
    """Parse valid YAML or the one known auto-discovery marker defect."""
    if not text.startswith("---\n"):
        raise FrontmatterError("document must begin with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise FrontmatterError("frontmatter closing delimiter is missing")
    raw = text[4:end]
    body = text[end + 5 :]
    try:
        return _load_legacy_mapping(raw), body, None
    except FrontmatterError as original:
        lines = raw.splitlines()
        markers = [index for index, line in enumerate(lines) if line.strip() == _AUTO_DISCOVERY_MARKER]
        if len(markers) != 1:
            raise original
        marker = markers[0]
        primary = _load_legacy_mapping("\n".join(lines[:marker]))
        supplemental = _load_legacy_mapping("\n".join(lines[marker + 1 :]))
        conflicts = sorted(
            key for key, value in supplemental.items() if key in primary and primary[key] != value
        )
        for key, value in supplemental.items():
            primary.setdefault(key, value)
        return primary, body, {
            "kind": "auto-discovery-marker",
            "conflicting_keys": conflicts,
        }


def _is_operational(relative: Path) -> bool:
    lowered = tuple(part.lower() for part in relative.parts)
    return (
        lowered[0] in _OPERATIONAL_ROOTS
        or any(part in {"node_modules", "__pycache__"} for part in lowered)
        or relative.name.lower() in _OPERATIONAL_FILES
        or relative.name.startswith("._")
        or lowered[:2] == ("indexes", "generated")
    )


def _walk_read_only(root: Path, max_files: int) -> tuple[list[Path], list[str], int, int]:
    files: list[Path] = []
    symlinks: list[str] = []
    ignored_internal = 0
    ignored_operational = 0
    visited = 0
    pending = [(root, False, False)]
    while pending:
        directory, inside_internal, inside_operational = pending.pop()
        for child in sorted(directory.iterdir(), key=lambda item: item.name, reverse=True):
            visited += 1
            if visited > max_files:
                raise MigrationError("vault exceeds the configured file limit")
            relative = child.relative_to(root)
            child_internal = inside_internal or relative.parts[0].lower() in _INTERNAL_ROOTS
            child_operational = inside_operational or _is_operational(relative)
            if child.is_symlink():
                if child_internal:
                    ignored_internal += 1
                elif child_operational:
                    ignored_operational += 1
                else:
                    symlinks.append(relative.as_posix())
            elif child.is_dir():
                pending.append((child, child_internal, child_operational))
                continue
            elif child.is_file():
                if child_internal:
                    ignored_internal += 1
                elif child_operational:
                    ignored_operational += 1
                else:
                    files.append(child)
            else:
                continue
    return (
        sorted(files, key=lambda path: path.relative_to(root).as_posix()),
        sorted(symlinks),
        ignored_internal,
        ignored_operational,
    )


def inventory_vault(root: Path | str, *, max_files: int = 100_000) -> dict[str, Any]:
    """Inspect a vault without writing files or returning note bodies."""
    supplied = Path(root)
    if supplied.is_symlink():
        raise MigrationError("vault root cannot be a symlink")
    vault = supplied.resolve()
    if not vault.is_dir():
        raise MigrationError("vault root must be an existing directory")
    if max_files < 1:
        raise MigrationError("file limit must be positive")

    files, symlinks, ignored_internal, ignored_operational = _walk_read_only(vault, max_files)
    frontmatter = Counter(valid=0, missing=0, invalid=0, oversized=0)
    schema_versions: Counter[str] = Counter()
    sensitivities: Counter[str] = Counter()
    identifiers: dict[str, list[str]] = defaultdict(list)
    canonical_validation = Counter(valid=0, invalid=0)
    markdown_entries: list[dict[str, str]] = []
    other_entries: list[str] = []
    extension_counts: Counter[str] = Counter()
    canonical_markdown = 0
    legacy_markdown = 0

    for path in files:
        relative = path.relative_to(vault).as_posix()
        suffix = path.suffix.lower() or "[none]"
        extension_counts[suffix] += 1
        if suffix != ".md":
            other_entries.append(relative)
            continue
        canonical = Path(relative).parts[0] in ALLOWED_CANONICAL_FOLDERS
        canonical_markdown += int(canonical)
        legacy_markdown += int(not canonical)
        entry = {"path": relative, "classification": "canonical" if canonical else "legacy"}
        if path.stat().st_size > MAX_MARKDOWN_BYTES:
            frontmatter["oversized"] += 1
            entry["frontmatter"] = "oversized"
            if canonical:
                canonical_validation["invalid"] += 1
                entry["canonical_validation"] = "invalid"
            markdown_entries.append(entry)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            frontmatter["invalid"] += 1
            entry["frontmatter"] = "invalid"
            if canonical:
                canonical_validation["invalid"] += 1
                entry["canonical_validation"] = "invalid"
            markdown_entries.append(entry)
            continue
        if not text.startswith("---"):
            frontmatter["missing"] += 1
            entry["frontmatter"] = "missing"
        else:
            try:
                metadata, _ = parse_frontmatter(text)
            except FrontmatterError:
                frontmatter["invalid"] += 1
                entry["frontmatter"] = "invalid"
            else:
                frontmatter["valid"] += 1
                entry["frontmatter"] = "valid"
                record_id = metadata.get("id")
                if isinstance(record_id, str) and record_id:
                    identifiers[record_id].append(relative)
                schema_version = metadata.get("schema_version")
                if schema_version is not None:
                    schema_versions[str(schema_version)] += 1
                sensitivity = metadata.get("sensitivity")
                if sensitivity is not None:
                    sensitivities[str(sensitivity)] += 1
        if canonical:
            try:
                validate_canonical_text(text, relative)
                canonical_validation["valid"] += 1
                entry["canonical_validation"] = "valid"
            except CanonicalValidationError:
                canonical_validation["invalid"] += 1
                entry["canonical_validation"] = "invalid"
        markdown_entries.append(entry)

    duplicate_ids = {
        record_id: sorted(paths)
        for record_id, paths in sorted(identifiers.items())
        if len(paths) > 1
    }
    return {
        "inventory_version": 1,
        "mode": "read-only",
        "writes_performed": False,
        "scanned_files": len(files),
        "markdown_files": canonical_markdown + legacy_markdown,
        "canonical_markdown": canonical_markdown,
        "legacy_markdown": legacy_markdown,
        "other_files": len(other_entries),
        "ignored_internal_files": ignored_internal,
        "ignored_operational_files": ignored_operational,
        "symlinks": symlinks,
        "frontmatter": dict(frontmatter),
        "canonical_validation": dict(canonical_validation),
        "schema_versions": dict(sorted(schema_versions.items())),
        "sensitivities": dict(sorted(sensitivities.items())),
        "extension_counts": dict(sorted(extension_counts.items())),
        "duplicate_ids": duplicate_ids,
        "markdown_entries": markdown_entries,
        "other_entries": other_entries,
    }


def plan_migration(
    root: Path | str,
    *,
    action_limit: int = 1_000,
    max_files: int = 100_000,
) -> dict[str, Any]:
    """Produce an explicit bounded migration plan without changing the vault."""
    inventory = inventory_vault(root, max_files=max_files)
    actions: list[dict[str, Any]] = []

    for path in inventory["symlinks"]:
        actions.append({"action": "manual_symlink_review", "path": path, "reason": "symlink-not-followed"})
    for record_id, paths in inventory["duplicate_ids"].items():
        actions.append({"action": "resolve_duplicate_id", "record_id": record_id, "paths": paths})
    for entry in inventory["markdown_entries"]:
        if entry["classification"] == "canonical":
            if entry.get("canonical_validation") == "invalid":
                actions.append(
                    {
                        "action": "repair_canonical_record",
                        "path": entry["path"],
                        "reason": "specialized-schema-validation-failed",
                    }
                )
            continue
        action = "map_legacy_record" if entry["frontmatter"] == "valid" else "assign_metadata"
        actions.append({"action": action, "path": entry["path"], "reason": entry["frontmatter"]})
    for path in inventory["other_entries"]:
        actions.append({"action": "preserve_source", "path": path, "reason": "non-markdown-source"})

    actions.sort(key=lambda item: (str(item["action"]), str(item.get("path", item.get("record_id", "")))))
    bounded_limit = max(1, min(int(action_limit), 10_000))
    return {
        "plan_version": 1,
        "mode": "dry-run",
        "writes_performed": False,
        "inventory": inventory,
        "actions": actions[:bounded_limit],
        "actions_truncated": len(actions) > bounded_limit,
        "summary": {
            "total_actions": len(actions),
            "returned_actions": min(len(actions), bounded_limit),
        },
    }


def _deterministic_ulid(seed: str) -> str:
    value = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:16], "big")
    chars = ["0"] * 26
    for index in range(25, -1, -1):
        chars[index] = _ULID_ALPHABET[value & 31]
        value >>= 5
    return "".join(chars)


def _mapped_sensitivity(value: object) -> str:
    if isinstance(value, str):
        return _SENSITIVITY_MAP.get(value.strip().lower(), "restricted")
    return "restricted"


def _timestamp(value: object, fallback: datetime) -> str:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed_date = date.fromisoformat(value)
            except ValueError:
                parsed = None
            else:
                parsed = datetime(
                    parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=timezone.utc
                )
    if parsed is None:
        parsed = fallback
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _entity_target(relative: str, record_type: str) -> str:
    stem = Path(relative).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "untitled"
    if not stem.startswith(f"{record_type}-"):
        stem = f"{record_type}-{stem}"
    return f"entities/{stem}.md"


def build_mapping_plan(root: Path | str, *, max_files: int = 100_000) -> dict[str, Any]:
    """Compile deterministic private mappings without returning note bodies or writing files."""
    vault = Path(root).resolve()
    inventory = inventory_vault(vault, max_files=max_files)
    duplicate_paths = {path for paths in inventory["duplicate_ids"].values() for path in paths}
    mappings: list[dict[str, Any]] = []

    for entry in inventory["markdown_entries"]:
        relative = entry["path"]
        path = vault / relative
        mapping: dict[str, Any] = {
            "source_path": relative,
            "source_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if entry["frontmatter"] in {"missing", "oversized"}:
            mapping.update(disposition="quarantine", reason=f"frontmatter-{entry['frontmatter']}")
            mappings.append(mapping)
            continue
        text = path.read_text(encoding="utf-8")
        repair: dict[str, object] | None = None
        try:
            if entry["frontmatter"] == "valid":
                metadata, _ = parse_frontmatter(text)
            else:
                metadata, _, repair = parse_legacy_frontmatter(text)
        except FrontmatterError:
            mapping.update(disposition="quarantine", reason=f"frontmatter-{entry['frontmatter']}")
            mappings.append(mapping)
            continue
        if repair is not None:
            mapping["repair"] = repair
        sensitivity = _mapped_sensitivity(metadata.get("sensitivity"))
        mapping["proposed_sensitivity"] = sensitivity
        first_folder = Path(relative).parts[0].lower()
        metadata_type = metadata.get("type")
        normalized_type = (
            metadata_type.strip().lower().replace("_", "-")
            if isinstance(metadata_type, str) and metadata_type.strip()
            else ""
        )
        entity_type = _ENTITY_FOLDERS.get(first_folder)
        if first_folder == "source-items" and normalized_type in {
            "company",
            "organization",
            "person",
        }:
            entity_type = normalized_type
        is_source_item = first_folder == "source-items" and normalized_type in {
            "source-item",
            "sourceitem",
        }
        if entity_type is None and not is_source_item:
            disposition = (
                "defer_specialized_schema"
                if entry["classification"] == "canonical"
                else "preserve_legacy"
            )
            mapping.update(disposition=disposition, reason="unsupported-v0.1-record-type")
            mappings.append(mapping)
            continue

        record_type = entity_type or normalized_type or "entity"
        legacy_id = metadata.get("id")
        legacy_id_text = legacy_id if isinstance(legacy_id, str) and legacy_id else ""
        if (
            repair is None
            and _ULID_PATTERN.fullmatch(legacy_id_text)
            and relative not in duplicate_paths
        ):
            proposed_id = legacy_id_text
        else:
            proposed_id = _deterministic_ulid(f"path:{relative}\0legacy-id:{legacy_id_text}")
        title = next(
            (
                value.strip()
                for key in ("title", "name", "company_name", "org_name")
                if isinstance((value := metadata.get(key)), str) and value.strip()
            ),
            Path(relative).stem.replace("-", " ").strip().title(),
        )
        status = metadata.get("status")
        if not isinstance(status, str) or not status.strip():
            status = "migration-review"
        fallback = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        created = _timestamp(metadata.get("created_at"), fallback)
        updated = _timestamp(metadata.get("updated_at", metadata.get("last_updated")), fallback)
        if datetime.fromisoformat(updated.replace("Z", "+00:00")) < datetime.fromisoformat(
            created.replace("Z", "+00:00")
        ):
            created = updated
        proposed_metadata = {
            "schema_version": "0.1",
            "id": proposed_id,
            "type": record_type,
            "title": title,
            "status": status.strip(),
            "sensitivity": sensitivity,
            "created_at": created,
            "updated_at": updated,
        }
        if entity_type is not None:
            mapping.update(
                disposition="candidate_entity",
                target_path=_entity_target(relative, record_type),
                legacy_id=legacy_id_text or None,
                proposed_metadata=proposed_metadata,
            )
        else:
            source_url = metadata.get("source_url", metadata.get("url"))
            if isinstance(source_url, str) and source_url.startswith(("http://", "https://")):
                proposed_metadata["source_url"] = source_url
            source_parts = Path(relative).parts[1:]
            source_subpath = Path(*source_parts).as_posix()
            proposed_metadata.update(
                {
                    "type": "source-item",
                    "source_hash": mapping["source_hash"],
                    "original_path": f"sources/legacy-source-items/{source_subpath}",
                    "media_type": "text/markdown",
                }
            )
            mapping.update(
                disposition="candidate_source_item",
                target_path=f"source-items/{source_subpath}",
                legacy_id=legacy_id_text or None,
                source_basis="preserved-legacy-note",
                proposed_metadata=proposed_metadata,
            )
        mappings.append(mapping)

    target_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mapping in mappings:
        if mapping["disposition"] in {"candidate_entity", "candidate_source_item"}:
            target_groups[mapping["target_path"]].append(mapping)
    for target, group in target_groups.items():
        if len(group) < 2:
            continue
        target_path = Path(target)
        for index, mapping in enumerate(sorted(group, key=lambda item: item["source_path"]), start=1):
            mapping["target_path"] = (
                target_path.parent / f"{target_path.stem}-{index:02d}{target_path.suffix}"
            ).as_posix()
            mapping["target_collision_resolved"] = True

    for relative in inventory["other_entries"]:
        path = vault / relative
        mappings.append(
            {
                "source_path": relative,
                "source_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                "disposition": "preserve_source",
            }
        )
    for relative in inventory["symlinks"]:
        mappings.append({"source_path": relative, "disposition": "manual_symlink_review"})

    mappings.sort(key=lambda item: item["source_path"])
    counts = Counter(item["disposition"] for item in mappings)
    return {
        "mapping_version": 1,
        "mode": "read-only",
        "source_writes_performed": False,
        "mappings": mappings,
        "summary": {"total": len(mappings), "by_disposition": dict(sorted(counts.items()))},
    }


def _write_rehearsal_file(root: Path, relative: str, data: bytes) -> str:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def rehearse_migration(
    root: Path | str,
    destination: Path | str,
    *,
    confirm_disposable: bool = False,
    max_files: int = 100_000,
) -> dict[str, Any]:
    """Materialize a candidate migration only in a new disposable destination."""
    if not confirm_disposable:
        raise MigrationError("disposable rehearsal requires explicit confirmation")
    supplied_source = Path(root)
    if supplied_source.is_symlink():
        raise MigrationError("vault root cannot be a symlink")
    source = supplied_source.resolve()
    target = Path(os.path.abspath(destination))
    if target.is_symlink():
        raise MigrationError("disposable destination cannot be a symlink")
    if source == target or source in target.parents or target in source.parents:
        raise MigrationError("source and disposable destination cannot overlap")
    if target.exists():
        raise MigrationError("disposable destination must not exist")
    current = target.parent
    while current != current.parent:
        if current.is_symlink():
            raise MigrationError("disposable destination cannot contain symlink components")
        current = current.parent
    plan = build_mapping_plan(source, max_files=max_files)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    journal: list[dict[str, Any]] = []
    try:
        for mapping in plan["mappings"]:
            disposition = mapping["disposition"]
            if disposition == "manual_symlink_review":
                journal.append(
                    {
                        "source_path": mapping["source_path"],
                        "disposition": disposition,
                        "copied": False,
                    }
                )
                continue
            relative = mapping["source_path"]
            source_path = source / relative
            data = source_path.read_bytes()
            source_hash = hashlib.sha256(data).hexdigest()
            if source_hash != mapping["source_hash"]:
                raise MigrationError(f"source changed during rehearsal: {relative}")
            preserved_relative = f"preserved/{relative}"
            _write_rehearsal_file(stage, preserved_relative, data)

            candidate_relative: str
            candidate_data: bytes
            provenance_relative: str | None = None
            if disposition in {"candidate_entity", "candidate_source_item"}:
                source_text = data.decode("utf-8")
                if mapping.get("repair"):
                    _, body, _ = parse_legacy_frontmatter(source_text)
                else:
                    _, body = parse_frontmatter(source_text)
                if not body.strip():
                    body = f"# {mapping['proposed_metadata']['title']}\n"
                candidate_relative = f"candidate-vault/{mapping['target_path']}"
                candidate_text = render_frontmatter(mapping["proposed_metadata"], body)
                validate_canonical_text(candidate_text, mapping["target_path"])
                candidate_data = candidate_text.encode("utf-8")
                if disposition == "candidate_source_item":
                    provenance_relative = (
                        f"candidate-vault/{mapping['proposed_metadata']['original_path']}"
                    )
                    _write_rehearsal_file(stage, provenance_relative, data)
            elif disposition == "preserve_legacy":
                candidate_relative = f"candidate-vault/legacy/{relative}"
                candidate_data = data
            elif disposition in {"quarantine", "defer_specialized_schema"}:
                candidate_relative = f"candidate-vault/quarantine/{relative}"
                candidate_data = data
            elif disposition == "preserve_source":
                candidate_relative = f"candidate-vault/sources/{relative}"
                candidate_data = data
            else:
                raise MigrationError(f"unsupported rehearsal disposition: {disposition}")
            output_hash = _write_rehearsal_file(stage, candidate_relative, candidate_data)
            if hashlib.sha256(source_path.read_bytes()).hexdigest() != source_hash:
                raise MigrationError(f"source changed during rehearsal: {relative}")
            journal_entry = {
                "source_path": relative,
                "source_hash": source_hash,
                "preserved_path": preserved_relative,
                "candidate_path": candidate_relative,
                "candidate_hash": output_hash,
                "disposition": disposition,
            }
            if provenance_relative is not None:
                journal_entry["provenance_path"] = provenance_relative
                journal_entry["provenance_hash"] = source_hash
            journal.append(journal_entry)

        _write_rehearsal_file(
            stage,
            "migration-plan.private.json",
            (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        _write_rehearsal_file(
            stage,
            "migration-journal.private.json",
            (json.dumps({"version": 1, "entries": journal}, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        if build_mapping_plan(source, max_files=max_files) != plan:
            raise MigrationError("source tree changed during rehearsal")
        if target.exists() or target.is_symlink():
            raise MigrationError("disposable destination appeared during rehearsal")
        os.replace(stage, target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {
        "rehearsal_version": 1,
        "source_writes_performed": False,
        "destination_writes_performed": True,
        "destination": str(target),
        "summary": plan["summary"],
    }
