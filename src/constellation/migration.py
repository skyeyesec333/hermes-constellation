"""Read-only inventory and dry-run planning for legacy vault migration."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .frontmatter import FrontmatterError, parse_frontmatter
from .validation import ALLOWED_CANONICAL_FOLDERS, CanonicalValidationError, validate_canonical_text

MAX_MARKDOWN_BYTES = 10 * 1024 * 1024
_INTERNAL_ROOTS = {".constellation"}
_OPERATIONAL_ROOTS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}
_OPERATIONAL_FILES = {".ds_store"}


class MigrationError(RuntimeError):
    """Raised when a vault cannot be inventoried safely."""


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
