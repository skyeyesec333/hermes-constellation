"""Local, consent-based Operator Context storage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .storage import atomic_write_text, safe_relative_path
from .vault import is_initialized

OPERATOR_CONTEXT_RELATIVE = Path(".constellation/operator-context.yaml")


class OperatorContextError(RuntimeError):
    """Raised when an operator context cannot be safely staged."""


class OperatorContext(BaseModel):
    """Explicit local relevance context; it never triggers research or egress."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    status: Literal["draft", "active"] = "draft"
    reviewed_at: datetime | None = None
    roles: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    geographies: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    strategic_priorities: list[str] = Field(default_factory=list)
    relationship_objectives: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def active_context_requires_review_timestamp(self) -> "OperatorContext":
        if self.status == "active" and self.reviewed_at is None:
            raise ValueError("active operator context requires reviewed_at")
        return self

    @field_validator(
        "roles",
        "companies",
        "geographies",
        "sectors",
        "strategic_priorities",
        "relationship_objectives",
        "exclusions",
    )
    @classmethod
    def values_are_nonblank_and_unique(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("operator context values cannot be blank")
        if len({value.casefold() for value in cleaned}) != len(cleaned):
            raise ValueError("operator context values must be unique")
        return cleaned


def _context_path(root: Path | str) -> Path:
    if not is_initialized(root):
        raise OperatorContextError("operator context requires an initialized vault")
    return safe_relative_path(root, OPERATOR_CONTEXT_RELATIVE)


def delete_operator_context(root: Path | str, *, confirm: bool) -> dict[str, str]:
    """Delete the local profile only after explicit confirmation."""
    if not confirm:
        raise OperatorContextError("explicit confirmation is required")
    path = _context_path(root)
    if not path.exists():
        return {"status": "absent"}
    if not path.is_file() or path.is_symlink():
        raise OperatorContextError("operator context is not a safe regular file")
    path.unlink()
    return {"status": "absent"}


def operator_context_status(root: Path | str) -> dict[str, int | str]:
    """Return only profile lifecycle metadata, never profile contents."""
    path = _context_path(root)
    if not path.exists():
        return {"status": "absent"}
    if not path.is_file() or path.is_symlink():
        raise OperatorContextError("operator context is not a safe regular file")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise OperatorContextError("operator context YAML is unreadable") from exc
    if not isinstance(raw, dict):
        raise OperatorContextError("operator context YAML must contain a mapping")
    context = OperatorContext.model_validate(raw)
    return {"status": context.status, "version": context.version}


def activate_operator_context(root: Path | str, *, confirm: bool) -> OperatorContext:
    """Activate a staged profile only after explicit local confirmation."""
    if not confirm:
        raise OperatorContextError("explicit confirmation is required")
    path = _context_path(root)
    if not path.is_file() or path.is_symlink():
        raise OperatorContextError("operator context draft does not exist")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise OperatorContextError("operator context YAML is unreadable") from exc
    if not isinstance(raw, dict):
        raise OperatorContextError("operator context YAML must contain a mapping")
    context = OperatorContext.model_validate(
        {**raw, "status": "active", "reviewed_at": datetime.now(UTC)}
    )
    text = yaml.safe_dump(
        context.model_dump(mode="json"), allow_unicode=True, default_flow_style=False, sort_keys=True
    )
    atomic_write_text(root, OPERATOR_CONTEXT_RELATIVE, text)
    return context


def stage_operator_context(root: Path | str, input_path: Path | str) -> OperatorContext:
    """Validate a user-supplied local profile and atomically save it as a draft."""
    source = Path(input_path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise OperatorContextError("operator context input must be a regular file")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise OperatorContextError("operator context input YAML is unreadable") from exc
    if not isinstance(raw, dict):
        raise OperatorContextError("operator context input YAML must contain a mapping")
    context = OperatorContext.model_validate(raw).model_copy(update={"status": "draft"})
    text = yaml.safe_dump(
        context.model_dump(mode="json"), allow_unicode=True, default_flow_style=False, sort_keys=True
    )
    atomic_write_text(root, OPERATOR_CONTEXT_RELATIVE, text)
    return context
