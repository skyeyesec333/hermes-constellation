"""One-way, allowlist-driven public release compiler."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml


class ReleaseError(RuntimeError):
    """Raised when a release cannot be built safely."""


def _safe_relative(value: str, *, allow_root: bool = False) -> Path:
    path = Path(value)
    if allow_root and value == ".":
        return path
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ReleaseError(f"Unsafe manifest path: {value}")
    return path


def _files_under(root: Path, relative: Path) -> set[Path]:
    target = root / relative
    if target.is_symlink():
        raise ReleaseError(f"Release input is a symlink: {relative}")
    if target.is_file():
        return {relative}
    if not target.is_dir():
        raise ReleaseError(f"Public root does not exist: {relative}")
    found: set[Path] = set()
    for item in target.rglob("*"):
        if item.is_symlink():
            raise ReleaseError(f"Release input is a symlink: {item.relative_to(root)}")
        if item.is_file():
            found.add(item.relative_to(root))
    return found


def build_release(source: Path, destination: Path, manifest_path: Path) -> dict[str, Any]:
    """Copy a declared public tree into an empty destination, failing closed."""
    source = source.resolve()
    if destination.is_symlink():
        raise ReleaseError("Release destination cannot be a symlink")
    destination = destination.resolve(strict=False)
    if destination == source or destination.is_relative_to(source):
        raise ReleaseError("Release destination cannot be inside the source tree")
    if destination.exists() and not destination.is_dir():
        raise ReleaseError("Release destination must be a directory")
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_relative_to(source):
        raise ReleaseError("Manifest must be inside the source root")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    entries = manifest.get("files") or {}
    roots = manifest.get("public_roots") or []
    if not isinstance(entries, dict) or not roots:
        raise ReleaseError("Manifest requires files and public_roots")

    allowed = {_safe_relative(str(name)) for name in entries}
    candidates: set[Path] = set()
    for root_name in roots:
        candidates.update(_files_under(source, _safe_relative(str(root_name), allow_root=True)))
    manifest_relative = manifest_path.relative_to(source)
    if manifest_relative not in allowed:
        candidates.discard(manifest_relative)

    unknown = sorted(candidates - allowed)
    if unknown:
        raise ReleaseError(f"Unknown public file: {unknown[0].as_posix()}")
    missing = sorted(allowed - candidates)
    if missing:
        raise ReleaseError(f"Allowlisted file is missing: {missing[0].as_posix()}")

    if destination.exists() and any(destination.iterdir()):
        raise ReleaseError("Release destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for relative in sorted(allowed):
        src = source / relative
        if src.is_symlink() or not src.is_file():
            raise ReleaseError(f"Release input is not a regular file: {relative}")
        dest = destination / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(relative.as_posix())
    return {"version": 1, "copied": copied, "destination": str(destination)}
