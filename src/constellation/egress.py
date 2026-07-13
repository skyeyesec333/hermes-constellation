"""Deny-by-default model egress authorization and audit ledger."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml

from .models import Sensitivity, generate_ulid
from .storage import safe_relative_path
from .vault import CONFIG_RELATIVE, is_initialized

Purpose = Literal["stage1", "research", "evaluation", "embedding"]
Transport = Literal["local", "external"]
_PURPOSES = {"stage1", "research", "evaluation", "embedding"}
_SENSITIVITY_RANK = {
    Sensitivity.PUBLIC.value: 0,
    Sensitivity.INTERNAL.value: 1,
    Sensitivity.CONFIDENTIAL.value: 2,
    Sensitivity.RESTRICTED.value: 3,
}


class EgressError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EgressRequest:
    provider: str
    model: str
    purpose: Purpose
    sensitivity: Sensitivity | str
    source_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("provider and model are required")
        if self.purpose not in _PURPOSES:
            raise ValueError("unsupported egress purpose")
        try:
            sensitivity = Sensitivity(self.sensitivity)
        except ValueError as exc:
            raise ValueError("invalid sensitivity") from exc
        object.__setattr__(self, "sensitivity", sensitivity)
        if not self.source_hashes:
            raise ValueError("at least one source hash is required")
        for digest in self.source_hashes:
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("source hashes must be lowercase SHA-256 digests")

    def payload(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "purpose": self.purpose,
            "sensitivity": Sensitivity(self.sensitivity).value,
            "source_hashes": list(self.source_hashes),
        }


@dataclass(frozen=True, slots=True)
class EgressDecision:
    authorization_id: str
    allowed: bool
    reason: str
    provider: str
    model: str
    purpose: str
    transport: Transport | None
    sensitivity: str
    source_hashes: tuple[str, ...]
    request_sha256: str
    policy_sha256: str
    issued_at: str

    def payload(self) -> dict[str, object]:
        value = asdict(self)
        value["source_hashes"] = list(self.source_hashes)
        value["schema_version"] = "0.1"
        return value


class EgressDenied(EgressError):
    def __init__(self, decision: EgressDecision) -> None:
        self.decision = decision
        super().__init__(f"egress denied: {decision.reason}")


def _sha256_json(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _append_decision(vault: Path, decision: EgressDecision) -> None:
    path = safe_relative_path(vault, ".constellation/egress-ledger.jsonl")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise EgressError("egress ledger is unsafe")
    payload = (json.dumps(decision.payload(), sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise OSError("short write while recording egress decision")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _string_list(value: object) -> list[str] | None:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        return None
    return value


def _evaluate_policy(
    config: object, request: EgressRequest
) -> tuple[bool, str, Transport | None]:
    if not isinstance(config, dict) or "egress" not in config:
        return False, "egress_not_configured", None
    egress = config["egress"]
    if not isinstance(egress, dict) or set(egress) != {"external_enabled", "providers"}:
        return False, "policy_invalid", None
    if not isinstance(egress["external_enabled"], bool) or not isinstance(
        egress["providers"], dict
    ):
        return False, "policy_invalid", None
    providers = egress["providers"]
    for name, settings in providers.items():
        if not isinstance(name, str) or not name or not isinstance(settings, dict):
            return False, "policy_invalid", None
        if set(settings) != {
            "enabled",
            "transport",
            "max_sensitivity",
            "models",
            "purposes",
        }:
            return False, "policy_invalid", None
        if not isinstance(settings["enabled"], bool):
            return False, "policy_invalid", None
        if settings["transport"] not in {"local", "external"}:
            return False, "policy_invalid", None
        if settings["max_sensitivity"] not in _SENSITIVITY_RANK:
            return False, "policy_invalid", None
        models = _string_list(settings["models"])
        purposes = _string_list(settings["purposes"])
        if (
            models is None
            or purposes is None
            or any(purpose not in _PURPOSES for purpose in purposes)
        ):
            return False, "policy_invalid", None
    settings = providers.get(request.provider)
    if settings is None:
        return False, "provider_not_declared", None
    transport = settings["transport"]
    if not settings["enabled"]:
        return False, "provider_disabled", transport
    if transport == "external" and not egress["external_enabled"]:
        return False, "external_egress_disabled", transport
    if request.model not in settings["models"]:
        return False, "model_not_allowed", transport
    if request.purpose not in settings["purposes"]:
        return False, "purpose_not_allowed", transport
    if _SENSITIVITY_RANK[Sensitivity(request.sensitivity).value] > _SENSITIVITY_RANK[
        settings["max_sensitivity"]
    ]:
        return False, "sensitivity_exceeds_policy", transport
    return True, "allowed", transport


def authorize_egress(root: Path | str, request: EgressRequest) -> EgressDecision:
    """Evaluate and durably record one model-egress request."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise EgressError("vault is not initialized")
    config_path = safe_relative_path(vault, CONFIG_RELATIVE)
    config_bytes = config_path.read_bytes()
    policy_sha256 = hashlib.sha256(config_bytes).hexdigest()
    try:
        config = yaml.safe_load(config_bytes)
    except yaml.YAMLError:
        config = None
    allowed, reason, transport = _evaluate_policy(config, request)
    decision = EgressDecision(
        authorization_id=generate_ulid(),
        allowed=allowed,
        reason=reason,
        provider=request.provider,
        model=request.model,
        purpose=request.purpose,
        transport=transport,
        sensitivity=Sensitivity(request.sensitivity).value,
        source_hashes=request.source_hashes,
        request_sha256=_sha256_json(request.payload()),
        policy_sha256=policy_sha256,
        issued_at=datetime.now(UTC).isoformat(),
    )
    _append_decision(vault, decision)
    return decision


def require_egress(root: Path | str, request: EgressRequest) -> EgressDecision:
    """Return a recorded authorization or raise after recording its denial."""
    decision = authorize_egress(root, request)
    if not decision.allowed:
        raise EgressDenied(decision)
    return decision
