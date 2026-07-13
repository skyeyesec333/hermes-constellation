"""One-way, allowlist-driven public release compiler."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

import yaml

from .privacy import audit_tree


class ReleaseError(RuntimeError):
    """Raised when a release cannot be built safely."""


_ALLOWED_LINEAGES = frozenset(
    {
        "handwritten-generic",
        "public-license-text",
        "independently-fictional",
        "generated-from-python-schema",
        "release-lineage-manifest",
    }
)
_IGNORED_PARTS = frozenset(
    {
        ".git",
        ".hermes",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
    }
)
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})


def _safe_relative(value: str, *, allow_root: bool = False) -> Path:
    path = Path(value)
    if allow_root and value == ".":
        return path
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ReleaseError(f"Unsafe manifest path: {value}")
    return path


def _ignored(relative: Path) -> bool:
    return bool(set(relative.parts) & _IGNORED_PARTS) or relative.suffix.lower() in _IGNORED_SUFFIXES


def _files_under(root: Path, relative: Path) -> set[Path]:
    target = root / relative
    if target.is_symlink():
        raise ReleaseError(f"Release input is a symlink: {relative}")
    if target.is_file():
        return set() if _ignored(relative) else {relative}
    if not target.is_dir():
        raise ReleaseError(f"Public root does not exist: {relative}")
    found: set[Path] = set()
    for item in target.rglob("*"):
        item_relative = item.relative_to(root)
        if _ignored(item_relative):
            continue
        if item.is_symlink():
            raise ReleaseError(f"Release input is a symlink: {item_relative}")
        if item.is_file():
            found.add(item_relative)
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in files:
        record = json.dumps(
            {"path": item["path"], "sha256": item["sha256"]},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest.update(record.encode("utf-8") + b"\n")
    return digest.hexdigest()


def _read_manifest(path: Path) -> tuple[dict[Path, str], list[Path]]:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict) or manifest.get("version", 1) != 1:
        raise ReleaseError("Manifest version must be 1")
    entries = manifest.get("files")
    roots = manifest.get("public_roots")
    if not isinstance(entries, dict) or not entries or not isinstance(roots, list) or not roots:
        raise ReleaseError("Manifest requires non-empty files and public_roots")

    allowed: dict[Path, str] = {}
    for name, metadata in entries.items():
        relative = _safe_relative(str(name))
        if _ignored(relative):
            raise ReleaseError(f"Allowlist cannot contain runtime artifact: {relative}")
        if not isinstance(metadata, dict) or metadata.get("lineage") not in _ALLOWED_LINEAGES:
            raise ReleaseError(f"Invalid or missing lineage for: {relative}")
        allowed[relative] = str(metadata["lineage"])

    public_roots = [_safe_relative(str(root), allow_root=True) for root in roots]
    if len(set(public_roots)) != len(public_roots):
        raise ReleaseError("Manifest public_roots must be unique")
    return allowed, public_roots


def build_release(
    source: Path,
    destination: Path,
    manifest_path: Path,
    *,
    canaries: Iterable[str] = (),
) -> dict[str, Any]:
    """Compile, audit, hash, and atomically publish an exact public tree."""
    source_input = Path(source)
    if source_input.is_symlink() or not source_input.is_dir():
        raise ReleaseError("Release source must be a real directory")
    source = source_input.resolve()

    destination_input = Path(destination)
    if destination_input.is_symlink():
        raise ReleaseError("Release destination cannot be a symlink")
    destination = destination_input.resolve(strict=False)
    if destination == source or destination.is_relative_to(source):
        raise ReleaseError("Release destination cannot be inside the source tree")
    if destination.exists() and not destination.is_dir():
        raise ReleaseError("Release destination must be a directory")
    if destination.exists() and any(destination.iterdir()):
        raise ReleaseError("Release destination must be empty")

    manifest_input = Path(manifest_path)
    if manifest_input.is_symlink() or not manifest_input.is_file():
        raise ReleaseError("Manifest must be a regular file")
    manifest_path = manifest_input.resolve()
    if not manifest_path.is_relative_to(source):
        raise ReleaseError("Manifest must be inside the source root")
    allowed, roots = _read_manifest(manifest_path)

    candidates: set[Path] = set()
    for root in roots:
        candidates.update(_files_under(source, root))
    manifest_relative = manifest_path.relative_to(source)
    if manifest_relative not in allowed:
        candidates.discard(manifest_relative)

    unknown = sorted(candidates - set(allowed))
    if unknown:
        raise ReleaseError(f"Unknown public file: {unknown[0].as_posix()}")
    missing = sorted(set(allowed) - candidates)
    if missing:
        raise ReleaseError(f"Allowlisted file is missing: {missing[0].as_posix()}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".constellation-release-", dir=destination.parent))
    try:
        copied: list[str] = []
        file_records: list[dict[str, Any]] = []
        for relative in sorted(allowed):
            src = source / relative
            if src.is_symlink() or not src.is_file():
                raise ReleaseError(f"Release input is not a regular file: {relative}")
            dest = staging / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            copied.append(relative.as_posix())
            file_records.append(
                {
                    "path": relative.as_posix(),
                    "lineage": allowed[relative],
                    "sha256": _sha256(dest),
                    "bytes": dest.stat().st_size,
                }
            )

        staged_files = {
            path.relative_to(staging)
            for path in staging.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        if staged_files != set(allowed):
            raise ReleaseError("Staged release tree does not exactly match the allowlist")

        audit = audit_tree(staging, canaries=canaries)
        if not audit["passed"]:
            first = audit["findings"][0]
            raise ReleaseError(
                f"privacy audit failed: {first['path']} ({first['rule']})"
            )

        report = {
            "version": 2,
            "copied": copied,
            "files": file_records,
            "file_count": len(file_records),
            "manifest_sha256": _sha256(manifest_path),
            "tree_sha256": _tree_sha256(file_records),
            "audit": audit,
            "destination": str(destination),
        }
        if destination.exists():
            destination.rmdir()
        os.replace(staging, destination)
        return report
    finally:
        if staging.exists():
            shutil.rmtree(staging)
