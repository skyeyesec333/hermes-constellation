"""Legacy entity migration into canonical entities/ without data loss.

Phase 5 rework (2026-07-17): full-file inventory, classification reconciliation,
quarantine of unparseable files, fuzzy-match proposals, custom-metadata preservation,
old-ID→canonical-ID mapping, and detailed error classification.

Do NOT run execute_entity_migration(dry_run=False) against the live vault.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .frontmatter import FrontmatterError, parse_frontmatter, render_frontmatter
from .models import EntityKind, EntityRecord, Sensitivity
from .storage import atomic_write_text, safe_relative_path
from .validation import validate_canonical_text
from .vault import is_initialized

# Folders to scan for legacy entities
_LEGACY_FOLDERS: tuple[tuple[str, EntityKind], ...] = (
    ("people", EntityKind.PERSON),
    ("companies", EntityKind.COMPANY),
    ("organizations", EntityKind.ORGANIZATION),
)

# Canonical entity folder
_CANONICAL = "entities"

# Rehearsal/cutover backup folder
_REHEARSAL_DIR = ".constellation/migration-rehearsal"


class EntityMigrationError(RuntimeError):
    """Raised when entity migration encounters an unrecoverable state."""


def _inventory_physical_files(root: Path) -> dict[str, list[str]]:
    """Count every .md file in legacy folders, including unparseable ones.

    Returns {folder: [relative_path, ...]} for all physical .md files.
    """
    inventory: dict[str, list[str]] = {}
    for folder, _kind in _LEGACY_FOLDERS:
        base = root / folder
        if not base.is_dir():
            inventory[folder] = []
            continue
        paths = sorted(
            p.relative_to(root).as_posix()
            for p in base.glob("*.md")
            if p.is_file()
        )
        inventory[folder] = paths
    return inventory


def _read_legacy_file(
    root: Path, relative_path: str
) -> tuple[dict[str, object] | None, str | None, str | None]:
    """Attempt to read and parse one legacy file.

    Returns (metadata, body, error_reason). On success, metadata and body are
    populated and error_reason is None. On failure, both are None and
    error_reason is set.
    """
    path = root / relative_path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, None, f"read_error:{exc}"
    except UnicodeError as exc:
        return None, None, f"encoding_error:{exc}"
    try:
        metadata, body = parse_frontmatter(text)
    except FrontmatterError as exc:
        return None, None, f"frontmatter_error:{exc}"
    if not isinstance(metadata, dict):
        return None, None, "missing_or_invalid_frontmatter"
    return metadata, str(body).strip(), None


def _fuzzy_match(
    title: str, canonical_index: dict[str, dict[str, object]]
) -> list[dict[str, object]]:
    """Propose fuzzy-match candidates. Never auto-merge — proposals require review.

    Returns a list of {canonical_id, canonical_path, match_reason, score} sorted by score.
    """
    from difflib import SequenceMatcher

    title_lower = title.strip().casefold()
    candidates: list[dict[str, object]] = []
    for key, entry in canonical_index.items():
        ratio = SequenceMatcher(None, title_lower, key).ratio()
        if ratio >= 0.75:
            candidates.append({
                "canonical_id": entry["id"],
                "canonical_path": entry["path"],
                "match_reason": f"fuzzy:{ratio:.2f}",
                "score": ratio,
            })
    candidates.sort(key=lambda c: float(c["score"]), reverse=True)
    return candidates[:5]  # Top 5


def plan_entity_migration(root: Path | str) -> dict[str, object]:
    """Dry-run: inventory all physical legacy files, classify every one,
    propose matches, and report totals that reconcile to the physical count.
    """
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise EntityMigrationError("vault is not initialized")

    inventory = _inventory_physical_files(vault)
    total_physical = sum(len(v) for v in inventory.values())

    canonical_index = _canonical_entity_index(vault)

    report: dict[str, object] = {
        "schema_version": "0.1",
        "action": "plan",
        "total_physical_files": total_physical,
        "physical_inventory": inventory,
        "matched": [],
        "fuzzy_proposals": [],
        "new_entities": [],
        "quarantined": [],
        "skipped": [],
        "promotion_errors": [],
    }

    for folder, kind in _LEGACY_FOLDERS:
        for rel_path in inventory.get(folder, []):
            metadata, body, error = _read_legacy_file(vault, rel_path)
            if error:
                report["quarantined"].append({
                    "legacy_path": rel_path,
                    "reason": error,
                })
                continue
            assert metadata is not None and body is not None

            title = str(metadata.get("title", Path(rel_path).stem))
            title_key = title.strip().casefold()

            # Exact match in canonical index
            if title_key in canonical_index:
                canonical = canonical_index[title_key]
                report["matched"].append({
                    "legacy_path": rel_path,
                    "canonical_path": canonical["path"],
                    "canonical_id": canonical["id"],
                    "match_reason": "exact-title",
                })
                continue

            # Alias match
            aliases = metadata.get("aliases") if isinstance(metadata.get("aliases"), list) else []
            alias_found = False
            for alias in aliases:
                if isinstance(alias, str) and alias.strip().casefold() in canonical_index:
                    canonical = canonical_index[alias.strip().casefold()]
                    report["matched"].append({
                        "legacy_path": rel_path,
                        "canonical_path": canonical["path"],
                        "canonical_id": canonical["id"],
                        "match_reason": f"alias:{alias}",
                    })
                    alias_found = True
                    break
            if alias_found:
                continue

            # Fuzzy proposals
            fuzzy_candidates = _fuzzy_match(title, canonical_index)
            if fuzzy_candidates:
                report["fuzzy_proposals"].append({
                    "legacy_path": rel_path,
                    "legacy_title": title,
                    "candidates": fuzzy_candidates,
                })
                continue

            # No match — new entity
            report["new_entities"].append({
                "legacy_path": rel_path,
                "title": title,
                "kind": kind.value,
            })

    # Compute reconciling totals
    total_quarantined = len(report["quarantined"])
    total_matched = len(report["matched"])
    total_fuzzy = len(report["fuzzy_proposals"])
    total_new = len(report["new_entities"])
    total_classified = total_quarantined + total_matched + total_fuzzy + total_new

    report["totals"] = {
        "physical": total_physical,
        "classified": total_classified,
        "matched": total_matched,
        "fuzzy_proposals": total_fuzzy,
        "new": total_new,
        "quarantined": total_quarantined,
        "reconciled": total_physical == total_classified,
    }

    return report


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
                key = alias.strip().casefold()
                if key not in index:
                    index[key] = {
                        "id": entity_id,
                        "path": path.relative_to(root).as_posix(),
                        "title": str(metadata.get("title", "")),
                        "aliases": [],
                    }
    return index


def _promote_legacy_entity(
    vault: Path,
    legacy: dict[str, object],
    kind: EntityKind,
    now: datetime,
) -> dict[str, str]:
    """Create a canonical entity from a legacy record, preserving metadata."""
    legacy_path_str = str(legacy["legacy_path"])
    legacy_path = safe_relative_path(vault, legacy_path_str)

    try:
        metadata, body = parse_frontmatter(legacy_path.read_text(encoding="utf-8"))
    except (OSError, FrontmatterError, UnicodeError) as exc:
        raise EntityMigrationError(
            f"legacy entity cannot be read for promotion: {legacy_path_str}"
        ) from exc
    if not isinstance(metadata, dict):
        raise EntityMigrationError(f"legacy entity has no valid frontmatter: {legacy_path_str}")
    if not body.strip():
        raise EntityMigrationError(f"legacy entity body cannot be empty: {legacy_path_str}")

    # Preserve legacy ID and path for mapping
    legacy_id = str(metadata.get("id", ""))
    legacy_title = str(metadata.get("title", Path(legacy_path_str).stem))

    # Collect all custom metadata fields beyond the core set
    core_keys = {"id", "title", "aliases", "source_ids", "schema_version", "type", "status", "sensitivity"}
    custom_metadata = {k: v for k, v in metadata.items() if k not in core_keys}

    entity = EntityRecord(
        type=kind,
        title=legacy_title,
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        aliases=[str(a) for a in (metadata.get("aliases") or []) if isinstance(a, str)],
        source_ids=[str(s) for s in (metadata.get("source_ids") or []) if isinstance(s, str)],
        created_at=now,
        updated_at=now,
    )

    # Build body with legacy compatibility section
    compat_sections = []
    if legacy_id:
        compat_sections.append(f"**Legacy ID:** {legacy_id}")
    if legacy_path_str:
        compat_sections.append(f"**Legacy path:** {legacy_path_str}")
    if custom_metadata:
        compat_sections.append(f"**Legacy metadata:** {json.dumps(custom_metadata, sort_keys=True, default=str)}")

    compat_text = "\n".join(compat_sections) if compat_sections else ""
    body_with_compat = f"{body}\n\n---\n{compat_text}\n" if compat_text else f"{body}\n"

    target_path = f"{_CANONICAL}/{entity.id}.md"
    content = render_frontmatter(entity.model_dump(mode="json", exclude_none=True), body_with_compat)
    validate_canonical_text(content, target_path)
    atomic_write_text(vault, target_path, content)

    return {
        "legacy_path": legacy_path_str,
        "legacy_id": legacy_id,
        "entity_id": entity.id,
        "target_path": target_path,
    }


def execute_entity_migration(
    root: Path | str,
    *,
    dry_run: bool = True,
) -> dict[str, object]:
    """Execute or dry-run the entity migration plan.

    WARNING: Do not run with dry_run=False against the live vault.
    A separate live-vault recovery plan is required for the 151 already-promoted
    entities from the previous migration run.
    """
    if dry_run:
        return plan_entity_migration(root)

    vault = Path(root).absolute()
    plan = plan_entity_migration(vault)

    now = datetime.now().astimezone()
    journal: list[dict[str, object]] = []

    # Record matches
    for match in plan.get("matched", []):
        if not isinstance(match, dict):
            continue
        journal.append({
            "action": "matched",
            "legacy_path": match.get("legacy_path"),
            "canonical_path": match.get("canonical_path"),
            "canonical_id": match.get("canonical_id"),
            "reason": match.get("match_reason"),
            "timestamp": now.isoformat(),
        })

    # Record fuzzy proposals (requires review — not auto-promoted)
    for proposal in plan.get("fuzzy_proposals", []):
        if not isinstance(proposal, dict):
            continue
        journal.append({
            "action": "fuzzy_proposal",
            "legacy_path": proposal.get("legacy_path"),
            "legacy_title": proposal.get("legacy_title"),
            "candidates": proposal.get("candidates"),
            "requires_review": True,
            "timestamp": now.isoformat(),
        })

    # Record quarantined
    for entry in plan.get("quarantined", []):
        if not isinstance(entry, dict):
            continue
        journal.append({
            "action": "quarantined",
            "legacy_path": entry.get("legacy_path"),
            "reason": entry.get("reason"),
            "timestamp": now.isoformat(),
        })

    # Promote new entities
    promoted = 0
    errors = 0
    for new_entity in plan.get("new_entities", []):
        if not isinstance(new_entity, dict):
            continue
        try:
            kind_str = str(new_entity.get("kind", "person"))
            kind = EntityKind(kind_str)
            result = _promote_legacy_entity(vault, new_entity, kind, now)
            journal.append({
                "action": "promoted",
                "legacy_path": result["legacy_path"],
                "legacy_id": result["legacy_id"],
                "entity_id": result["entity_id"],
                "target_path": result["target_path"],
                "timestamp": now.isoformat(),
            })
            promoted += 1
        except EntityMigrationError as exc:
            journal.append({
                "action": "promotion_error",
                "legacy_path": new_entity.get("legacy_path"),
                "reason": str(exc),
                "timestamp": now.isoformat(),
            })
            errors += 1
        except Exception as exc:
            journal.append({
                "action": "promotion_error",
                "legacy_path": new_entity.get("legacy_path"),
                "reason": f"unexpected:{type(exc).__name__}:{exc}",
                "timestamp": now.isoformat(),
            })
            errors += 1

    # Write journal
    journal_path = safe_relative_path(vault, ".constellation/migration-journal.jsonl")
    with open(journal_path, "a", encoding="utf-8") as fh:
        for entry in journal:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    totals = plan.get("totals", {})
    return {
        "schema_version": "0.1",
        "matched": int(totals.get("matched", 0)),
        "fuzzy_proposals": int(totals.get("fuzzy_proposals", 0)),
        "promoted": promoted,
        "errors": errors,
        "quarantined": int(totals.get("quarantined", 0)),
        "total_physical": int(totals.get("physical", 0)),
        "reconciled": bool(totals.get("reconciled", False)),
        "journal_path": journal_path.relative_to(vault).as_posix(),
    }


def build_id_mapping(vault: Path) -> dict[str, str]:
    """Build old-legacy-ID → new-canonical-ID mapping from the migration journal.

    Used for repairing Wikilink and Dataview references after migration.
    """
    journal_path = vault / ".constellation/migration-journal.jsonl"
    if not journal_path.is_file():
        return {}

    mapping: dict[str, str] = {}
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("action") == "promoted":
            legacy_id = entry.get("legacy_id")
            entity_id = entry.get("entity_id")
            if legacy_id and entity_id:
                mapping[str(legacy_id)] = str(entity_id)
    return mapping
