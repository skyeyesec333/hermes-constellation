"""Legacy entity migration into canonical entities/ without data loss."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .frontmatter import FrontmatterError, parse_frontmatter
from .models import EntityKind, EntityRecord, Sensitivity
from .storage import atomic_write_text, safe_relative_path
from .vault import is_initialized

# Folders to scan for legacy entities
_LEGACY_FOLDERS: tuple[tuple[str, EntityKind], ...] = (
    ("people", EntityKind.PERSON),
    ("companies", EntityKind.COMPANY),
    ("organizations", EntityKind.ORGANIZATION),
)

# Canonical entity folder
_CANONICAL = "entities"


class EntityMigrationError(RuntimeError):
    """Raised when entity migration encounters an unrecoverable state."""


def _legacy_entities(root: Path, kind: EntityKind) -> list[dict[str, object]]:
    """Read legacy entities from a legacy folder."""
    base = root / kind.value if kind.value != "organization" else root / "organizations"
    if not base.exists():
        return []
    results: list[dict[str, object]] = []
    for path in sorted(base.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            metadata, body = parse_frontmatter(text)
        except (OSError, FrontmatterError, UnicodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        results.append(
            {
                "legacy_path": path.relative_to(root).as_posix(),
                "title": str(metadata.get("title", path.stem)),
                "aliases": metadata.get("aliases") if isinstance(metadata.get("aliases"), list) else [],
                "source_ids": metadata.get("source_ids") if isinstance(metadata.get("source_ids"), list) else [],
                "kind": kind.value,
                "metadata": metadata,
                "body": body.strip() if isinstance(body, str) else "",
            }
        )
    return results


def _canonical_entity_index(root: Path) -> dict[str, dict[str, object]]:
    """Build a lookup of canonical entities by title and aliases."""
    base = root / _CANONICAL
    if not base.exists():
        return {}
    index: dict[str, dict[str, object]] = {}
    for path in sorted(base.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            metadata, body = parse_frontmatter(text)
        except (OSError, FrontmatterError, UnicodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        entity_id = str(metadata.get("id", ""))
        title = str(metadata.get("title", "")).strip().casefold()
        if title:
            index[title] = {
                "id": entity_id,
                "path": path.relative_to(root).as_posix(),
                "title": str(metadata.get("title", "")),
                "aliases": [a.casefold() for a in (metadata.get("aliases") or []) if isinstance(a, str)],
            }
        for alias in (metadata.get("aliases") or []):
            if isinstance(alias, str) and alias.strip():
                index[alias.strip().casefold()] = index.get(title, {
                    "id": entity_id,
                    "path": path.relative_to(root).as_posix(),
                    "title": str(metadata.get("title", "")),
                    "aliases": [],
                })
    return index


def plan_entity_migration(root: Path | str) -> dict[str, object]:
    """Dry-run: scan legacy entities and report matches, conflicts, and new promotions."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise EntityMigrationError("vault is not initialized")

    canonical_index = _canonical_entity_index(vault)
    report: dict[str, object] = {
        "schema_version": "0.1",
        "action": "plan",
        "matched": [],
        "new_entities": [],
        "conflicts": [],
        "skipped": [],
        "total_legacy": 0,
        "total_matched": 0,
        "total_new": 0,
    }

    for folder, kind in _LEGACY_FOLDERS:
        for legacy in _legacy_entities(vault, kind):
            report["total_legacy"] = int(report["total_legacy"]) + 1
            title_key = str(legacy["title"]).strip().casefold()

            if title_key in canonical_index:
                canonical = canonical_index[title_key]
                report["matched"].append(
                    {
                        "legacy_path": legacy["legacy_path"],
                        "canonical_path": canonical["path"],
                        "canonical_id": canonical["id"],
                        "match_reason": "exact-title",
                    }
                )
                report["total_matched"] = int(report["total_matched"]) + 1
                continue

            # Check aliases
            found = False
            for alias in (legacy.get("aliases") or []):
                if isinstance(alias, str) and alias.strip().casefold() in canonical_index:
                    canonical = canonical_index[alias.strip().casefold()]
                    report["matched"].append(
                        {
                            "legacy_path": legacy["legacy_path"],
                            "canonical_path": canonical["path"],
                            "canonical_id": canonical["id"],
                            "match_reason": f"alias:{alias}",
                        }
                    )
                    report["total_matched"] = int(report["total_matched"]) + 1
                    found = True
                    break
            if found:
                continue

            # No match — will be promoted as new entity
            report["new_entities"].append(
                {
                    "legacy_path": legacy["legacy_path"],
                    "title": legacy["title"],
                    "kind": legacy["kind"],
                }
            )
            report["total_new"] = int(report["total_new"]) + 1

    return report


def _promote_legacy_entity(
    vault: Path,
    legacy: dict[str, object],
    kind: EntityKind,
    now: datetime,
) -> dict[str, str]:
    """Create a canonical entity from a legacy record."""
    entity = EntityRecord(
        type=kind,
        title=str(legacy["title"]),
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        aliases=[str(a) for a in (legacy.get("aliases") or []) if isinstance(a, str)],
        source_ids=[str(s) for s in (legacy.get("source_ids") or []) if isinstance(s, str)],
        created_at=now,
        updated_at=now,
    )
    target_path = f"{_CANONICAL}/{entity.id}.md"
    content = entity.model_dump_json(indent=2)
    atomic_write_text(vault, target_path, content)
    return {"legacy_path": str(legacy["legacy_path"]), "entity_id": entity.id, "target_path": target_path}


def execute_entity_migration(
    root: Path | str,
    *,
    dry_run: bool = True,
) -> dict[str, object]:
    """Execute or dry-run the entity migration plan."""
    if dry_run:
        return plan_entity_migration(root)

    vault = Path(root).absolute()
    plan = plan_entity_migration(vault)

    now = datetime.now().astimezone()
    journal: list[dict[str, object]] = []
    promoted = 0
    skipped = 0

    for match in plan.get("matched", []):
        if not isinstance(match, dict):
            continue
        journal.append(
            {
                "action": "matched",
                "legacy_path": match.get("legacy_path"),
                "canonical_path": match.get("canonical_path"),
                "canonical_id": match.get("canonical_id"),
                "reason": match.get("match_reason"),
                "timestamp": now.isoformat(),
            }
        )

    for new_entity in plan.get("new_entities", []):
        if not isinstance(new_entity, dict):
            continue
        try:
            kind_str = str(new_entity.get("kind", "person"))
            kind = EntityKind(kind_str)
            result = _promote_legacy_entity(vault, new_entity, kind, now)
            journal.append(
                {
                    "action": "promoted",
                    "legacy_path": result["legacy_path"],
                    "entity_id": result["entity_id"],
                    "target_path": result["target_path"],
                    "timestamp": now.isoformat(),
                }
            )
            promoted += 1
        except Exception:
            journal.append(
                {
                    "action": "skipped",
                    "legacy_path": new_entity.get("legacy_path"),
                    "reason": "promotion error",
                    "timestamp": now.isoformat(),
                }
            )
            skipped += 1

    journal_path = safe_relative_path(vault, ".constellation/migration-journal.jsonl")
    with open(journal_path, "a", encoding="utf-8") as fh:
        for entry in journal:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    return {
        "schema_version": "0.1",
        "matched": int(plan.get("total_matched", 0)),
        "promoted": promoted,
        "skipped": skipped,
        "total_legacy": int(plan.get("total_legacy", 0)),
        "journal_path": journal_path.relative_to(vault).as_posix(),
    }
