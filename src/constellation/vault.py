"""Safe, explicit Constellation vault initialization."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import SCHEMA_VERSION

CONFIG_RELATIVE = Path(".constellation/config.yaml")
STARTER_FOLDERS = (
    Path("Inbox/Files"),
    Path("Library/Files"),
    Path("Library/Text"),
    Path("source-items"),
    Path("claims"),
    Path("entities"),
    Path("relationships"),
    Path("research"),
    Path("interactions"),
    Path("decisions"),
    Path("inquiries"),
    Path("opportunities"),
    Path("analyses"),
    Path(".constellation/manifests"),
    Path(".constellation/candidates"),
    Path(".constellation/state"),
)


class VaultInitializationError(RuntimeError):
    pass


def _known_config(path: Path) -> bool:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(data, dict) and data.get("kind") == "constellation-vault" and data.get("schema_version") == SCHEMA_VERSION


def _reject_nested(root: Path) -> None:
    for parent in root.parents:
        if _known_config(parent / CONFIG_RELATIVE):
            raise VaultInitializationError("cannot initialize a vault inside another vault")


def initialize_vault(root: Path | str) -> list[Path]:
    """Initialize only a new/empty root; known vaults are idempotent."""
    target = Path(root).absolute()
    if target.is_symlink():
        raise VaultInitializationError("vault root cannot be a symlink")
    _reject_nested(target)
    config = target / CONFIG_RELATIVE
    if config.exists():
        if config.is_symlink() or not _known_config(config):
            raise VaultInitializationError("existing vault manifest is not recognized")
        return []
    if target.exists():
        if not target.is_dir():
            raise VaultInitializationError("vault root must be a directory")
        if any(target.iterdir()):
            raise VaultInitializationError("refusing to adopt a non-empty directory")
    else:
        target.mkdir(parents=True)
    created: list[Path] = []
    for relative in STARTER_FOLDERS:
        path = target / relative
        path.mkdir(parents=True, exist_ok=False)
        created.append(path)
    config_text = yaml.safe_dump(
        {
            "kind": "constellation-vault",
            "schema_version": SCHEMA_VERSION,
            "default_sensitivity": "internal",
            "source_registration": "review",
            "egress": {"external_enabled": False, "providers": {}},
        },
        sort_keys=True,
    )
    config.write_text(config_text, encoding="utf-8")
    created.append(config)
    return created


def is_initialized(root: Path | str) -> bool:
    target = Path(root).absolute()
    return not target.is_symlink() and _known_config(target / CONFIG_RELATIVE)
