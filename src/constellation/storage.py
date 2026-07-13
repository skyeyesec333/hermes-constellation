"""Filesystem primitives with containment, symlink, and conflict protection."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePath


class StorageError(RuntimeError):
    pass


class UnsafePathError(StorageError):
    pass


class ConflictError(StorageError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_symlink_components(path: Path, stop: Path) -> None:
    current = path
    while True:
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise UnsafePathError(f"symlink path component is not allowed: {current}")
        if current == stop:
            return
        if current.parent == current:
            raise UnsafePathError("path is not contained by vault root")
        current = current.parent


def safe_relative_path(root: Path | str, relative: Path | str) -> Path:
    """Resolve a lexical relative path without following any symlink."""
    root_path = Path(root).absolute()
    relative_path = Path(relative)
    if relative_path.is_absolute() or not relative_path.parts:
        raise UnsafePathError("path must be relative to the vault root")
    if any(part in {"", ".", ".."} for part in PurePath(relative_path).parts):
        raise UnsafePathError("path traversal is not allowed")
    _reject_symlink_components(root_path, root_path.anchor and Path(root_path.anchor) or root_path)
    destination = root_path.joinpath(relative_path)
    _reject_symlink_components(destination, root_path)
    return destination


def atomic_write_bytes(
    root: Path | str,
    relative: Path | str,
    data: bytes,
    *,
    expected_hash: str | None = None,
) -> Path:
    destination = safe_relative_path(root, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(destination.parent, Path(root).absolute())
    if destination.exists() and not destination.is_file():
        raise UnsafePathError("destination is not a regular file")
    if expected_hash is not None:
        current = sha256_file(destination) if destination.exists() else None
        if current != expected_hash:
            raise ConflictError("destination changed since it was reviewed")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if expected_hash is not None:
            current = sha256_file(destination) if destination.exists() else None
            if current != expected_hash:
                raise ConflictError("destination changed while the replacement was staged")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def atomic_write_text(
    root: Path | str,
    relative: Path | str,
    text: str,
    *,
    expected_hash: str | None = None,
) -> Path:
    return atomic_write_bytes(root, relative, text.encode("utf-8"), expected_hash=expected_hash)
