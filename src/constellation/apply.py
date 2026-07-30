"""Prepare and atomically activate a verified private migration cutover."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .frontmatter import parse_frontmatter
from .migration import build_mapping_plan
from .models import SCHEMA_VERSION
from .validation import ALLOWED_CANONICAL_FOLDERS, validate_vault
from .vault import STARTER_FOLDERS, is_initialized


class ApplyError(RuntimeError):
    """Raised when a migration apply safety gate fails."""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_canonical_cutover_path(relative: str) -> bool:
    """Canonical-folder check for cutover routing.

    people/ is canonical (person records); originals of migrated records and
    quarantined notes from canonical folders move under legacy/ or
    quarantine/ so the prepared vault passes canonical validation.
    """
    parts = Path(relative).parts
    return bool(parts) and parts[0] in ALLOWED_CANONICAL_FOLDERS


def tree_sha256(root: Path | str) -> str:
    """Hash regular file paths and bytes without following symlinks."""
    supplied = Path(root)
    if supplied.is_symlink():
        raise ApplyError("tree root cannot be a symlink")
    base = supplied.resolve()
    if not base.is_dir():
        raise ApplyError("tree root must be a directory")
    digest = hashlib.sha256()
    for current, directories, names in os.walk(base, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories):
            path = current_path / name
            if not path.is_symlink():
                kept.append(name)
        directories[:] = kept
        for name in sorted(names):
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(base).as_posix()
            file_hash = _hash_file(path)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_hash.encode("ascii"))
            digest.update(b"\n")
    return digest.hexdigest()


def _assert_no_symlink_components(path: Path) -> None:
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ApplyError("destination cannot contain symlink components")
        current = current.parent


def _write_file(root: Path, relative: str, data: bytes) -> str:
    destination = root / relative
    if destination.exists() or destination.is_symlink():
        raise ApplyError(f"cutover destination collision: {relative}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _copy_operational_tree(source: Path, stage: Path, relative_root: str) -> list[str]:
    root = source / relative_root
    skipped: list[str] = []
    if not root.exists():
        return skipped
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories):
            path = current_path / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                skipped.append(relative)
            else:
                kept.append(name)
        directories[:] = kept
        for name in sorted(names):
            path = current_path / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                skipped.append(relative)
                continue
            _write_file(stage, relative, path.read_bytes())
    return skipped


def _candidate_identity_check(root: Path) -> tuple[int, bool]:
    ids: list[str] = []
    for folder in ("entities", "source-items", "claims", "research", "interactions", "decisions", "inquiries", "opportunities", "analyses", "classifications", "watchlists", "snapshots", "observations", "events"):
        base = root / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            record_id = metadata.get("id")
            if isinstance(record_id, str):
                ids.append(record_id)
    return len(ids), len(ids) == len(set(ids))


def build_cutover_vault(
    source_root: Path | str,
    rehearsal_root: Path | str,
    destination: Path | str,
    *,
    expected_source_sha256: str,
    confirm_apply_staging: bool = False,
) -> dict[str, Any]:
    """Build a verified replacement vault without writing to the source vault."""
    if not confirm_apply_staging:
        raise ApplyError("apply staging requires explicit confirmation")
    supplied_source = Path(source_root)
    supplied_rehearsal = Path(rehearsal_root)
    if supplied_source.is_symlink() or supplied_rehearsal.is_symlink():
        raise ApplyError("source and rehearsal roots cannot be symlinks")
    source = supplied_source.resolve()
    rehearsal = supplied_rehearsal.resolve()
    target = Path(os.path.abspath(destination))
    if target.exists() or target.is_symlink():
        raise ApplyError("apply staging destination must not exist")
    for protected in (source, rehearsal):
        if protected == target or protected in target.parents or target in protected.parents:
            raise ApplyError("apply staging destination cannot overlap protected roots")
    _assert_no_symlink_components(target.parent)
    if tree_sha256(source) != expected_source_sha256:
        raise ApplyError("source tree hash does not match approved input")

    plan_path = rehearsal / "migration-plan.private.json"
    journal_path = rehearsal / "migration-journal.private.json"
    if not plan_path.is_file() or not journal_path.is_file():
        raise ApplyError("rehearsal bundle is missing its private plan or journal")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    current_plan = build_mapping_plan(source)
    if current_plan != plan:
        raise ApplyError("rehearsal mapping does not match the current source")
    journal_payload = json.loads(journal_path.read_text(encoding="utf-8"))
    journal = {entry["source_path"]: entry for entry in journal_payload["entries"]}

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    counts: Counter[str] = Counter()
    manual_symlinks: list[dict[str, str]] = []
    operational_symlinks: list[str] = []
    try:
        for mapping in plan["mappings"]:
            relative = mapping["source_path"]
            disposition = mapping["disposition"]
            counts[disposition] += 1
            if disposition == "manual_symlink_review":
                source_path = source / relative
                manual_symlinks.append({"path": relative, "target": os.readlink(source_path)})
                continue
            source_path = source / relative
            data = source_path.read_bytes()
            if hashlib.sha256(data).hexdigest() != mapping["source_hash"]:
                raise ApplyError(f"source changed during cutover staging: {relative}")
            entry = journal.get(relative)
            if entry is None:
                raise ApplyError(f"rehearsal journal entry is missing: {relative}")

            if disposition in {"candidate_entity", "candidate_source_item"}:
                candidate = rehearsal / entry["candidate_path"]
                if _hash_file(candidate) != entry["candidate_hash"]:
                    raise ApplyError(f"candidate hash mismatch: {relative}")
                _write_file(stage, mapping["target_path"], candidate.read_bytes())
                if disposition == "candidate_source_item":
                    provenance = rehearsal / entry["provenance_path"]
                    if _hash_file(provenance) != entry["provenance_hash"]:
                        raise ApplyError(f"provenance hash mismatch: {relative}")
                    _write_file(
                        stage,
                        mapping["proposed_metadata"]["original_path"],
                        provenance.read_bytes(),
                    )
                elif _is_canonical_cutover_path(relative):
                    _write_file(stage, f"legacy/{relative}", data)
                else:
                    _write_file(stage, relative, data)
            elif disposition in {"preserve_legacy", "preserve_source"}:
                _write_file(stage, relative, data)
            elif disposition in {"quarantine", "defer_specialized_schema"}:
                if _is_canonical_cutover_path(relative):
                    _write_file(stage, f"quarantine/{relative}", data)
                else:
                    _write_file(stage, relative, data)
            else:
                raise ApplyError(f"unsupported cutover disposition: {disposition}")

        operational_symlinks.extend(_copy_operational_tree(source, stage, ".obsidian"))
        config = source / ".constellation/config.yaml"
        if is_initialized(source):
            _write_file(stage, ".constellation/config.yaml", config.read_bytes())
        else:
            if config.is_file() and not config.is_symlink():
                _write_file(stage, ".migration/legacy-config.yaml", config.read_bytes())
            elif config.is_symlink():
                operational_symlinks.append(".constellation/config.yaml")
            canonical_config = yaml.safe_dump(
                {"kind": "constellation-vault", "schema_version": SCHEMA_VERSION},
                sort_keys=True,
            ).encode("utf-8")
            _write_file(stage, ".constellation/config.yaml", canonical_config)
        for folder in STARTER_FOLDERS:
            (stage / folder).mkdir(parents=True, exist_ok=True)
        if not is_initialized(stage):
            raise ApplyError("prepared cutover is missing its canonical vault manifest")

        validation = validate_vault(stage, limit=100_000)
        candidate_count, ids_unique = _candidate_identity_check(stage)
        if validation["invalid"] or not ids_unique:
            raise ApplyError("prepared cutover failed canonical validation or ID uniqueness")
        manifest = {
            "version": 1,
            "source_tree_sha256": expected_source_sha256,
            "mapping_summary": plan["summary"],
            "candidate_validation": {
                "valid": validation["valid"],
                "invalid": validation["invalid"],
            },
            "candidate_record_count": candidate_count,
            "candidate_ids_unique": ids_unique,
            "manual_symlinks": manual_symlinks,
            "operational_symlinks_skipped": operational_symlinks,
            "dispositions": dict(sorted(counts.items())),
        }
        _write_file(
            stage,
            ".migration/apply-manifest.private.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        if tree_sha256(source) != expected_source_sha256 or build_mapping_plan(source) != plan:
            raise ApplyError("source changed during cutover staging")
        if target.exists() or target.is_symlink():
            raise ApplyError("apply staging destination appeared concurrently")
        os.replace(stage, target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    return {
        "mode": "apply-staging",
        "source_writes_performed": False,
        "destination": str(target),
        "source_tree_sha256": expected_source_sha256,
        "candidate_validation": manifest["candidate_validation"],
        "candidate_ids_unique": ids_unique,
        "manual_symlinks": manual_symlinks,
    }


def _verify_prepared_vault(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    if not is_initialized(root):
        raise ApplyError("prepared vault is not initialized")
    validation = validate_vault(root, limit=100_000)
    count, unique = _candidate_identity_check(root)
    expected = manifest.get("candidate_validation", {})
    if (
        validation["valid"] != expected.get("valid")
        or validation["invalid"] != expected.get("invalid")
        or count != manifest.get("candidate_record_count")
        or unique is not True
        or manifest.get("candidate_ids_unique") is not True
    ):
        raise ApplyError("prepared vault no longer matches its verified apply manifest")
    return {"valid": validation["valid"], "invalid": validation["invalid"]}


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def activate_cutover(
    canonical_root: Path | str,
    prepared_root: Path | str,
    rollback_root: Path | str,
    *,
    expected_source_sha256: str,
    confirm_canonical_apply: bool = False,
) -> dict[str, Any]:
    """Atomically swap a verified prepared vault into the canonical path with rollback."""
    if not confirm_canonical_apply:
        raise ApplyError("canonical apply requires explicit confirmation")
    canonical = Path(os.path.abspath(canonical_root))
    prepared = Path(os.path.abspath(prepared_root))
    rollback = Path(os.path.abspath(rollback_root))
    if any(path.is_symlink() for path in (canonical, prepared, rollback)):
        raise ApplyError("canonical, prepared, and rollback paths cannot be symlinks")
    if not canonical.is_dir() or not prepared.is_dir():
        raise ApplyError("canonical and prepared vaults must be directories")
    if rollback.exists():
        raise ApplyError("rollback destination must not exist")
    if not (canonical.parent == prepared.parent == rollback.parent):
        raise ApplyError("canonical, prepared, and rollback paths must be siblings")
    _assert_no_symlink_components(canonical.parent)
    if tree_sha256(canonical) != expected_source_sha256:
        raise ApplyError("canonical source tree hash changed before activation")
    manifest_path = prepared / ".migration/apply-manifest.private.json"
    if not manifest_path.is_file():
        raise ApplyError("prepared vault is missing its apply manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_tree_sha256") != expected_source_sha256:
        raise ApplyError("prepared vault was built from a different source tree")
    validation = _verify_prepared_vault(prepared, manifest)
    if os.stat(canonical).st_dev != os.stat(prepared).st_dev:
        raise ApplyError("canonical and prepared vaults must be on the same filesystem")

    os.replace(canonical, rollback)
    _fsync_directory(canonical.parent)
    try:
        os.replace(prepared, canonical)
        _fsync_directory(canonical.parent)
        validation = _verify_prepared_vault(canonical, manifest)
    except Exception:
        if canonical.exists() and not prepared.exists():
            os.replace(canonical, prepared)
            _fsync_directory(canonical.parent)
        if rollback.exists() and not canonical.exists():
            os.replace(rollback, canonical)
            _fsync_directory(canonical.parent)
        raise

    return {
        "activated": True,
        "canonical": str(canonical),
        "rollback": str(rollback),
        "source_tree_sha256": expected_source_sha256,
        "candidate_validation": validation,
    }
