"""Review-only hypothesis packets (i2-successor Wave 4 Task 4.2).

A hypothesis is a bounded, review-only derived artifact — never canonical,
never promotable, never probabilistically scored in v1. Packets are seeded
from deterministic typology matches and carry:

- bounded evidence_for / evidence_against lists;
- non-probabilistic confidence bounds (``unverified-lead`` label only);
- concrete falsification checks;
- a 90-day expiry;
- an append-only review trail.

``refresh`` re-evaluates a packet against the current vault: missing
explaining edges refute it, passing the expiry date expires it, otherwise
it stays open with a recorded evaluation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import generate_ulid
from .storage import atomic_write_text
from .typologies import scan_typologies
from .vault import is_initialized

_PACKET_DIR = Path(".constellation/hypotheses")
_EVIDENCE_LIMIT = 10
_EXPIRY_DAYS = 90
_FALSIFICATION = [
    "If any explaining edge is superseded or removed, treat the hypothesis as refuted.",
    "If a canonical record contradicts the shape (e.g. an ownership claim reversing a link), review manually.",
    "If the underlying sources are retracted or marked stale, re-run generation before relying on this lead.",
]


class HypothesisError(RuntimeError):
    """Raised when hypothesis operations fail closed."""


def _packet_dir(vault: Path) -> Path:
    return vault / _PACKET_DIR


def _load_packets(vault: Path) -> list[tuple[Path, dict[str, Any]]]:
    base = _packet_dir(vault)
    packets: list[tuple[Path, dict[str, Any]]] = []
    if not base.is_dir():
        return packets
    for path in sorted(base.glob("hyp-*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == "hypothesis":
            packets.append((path, payload))
    return packets


def _origin_signature(match: dict[str, Any]) -> str:
    blob = f"{match['typology']}|{'|'.join(match['edge_ids'])}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _write_packet(vault: Path, packet: dict[str, Any]) -> None:
    atomic_write_text(
        vault,
        _PACKET_DIR / f"{packet['id']}.json",
        json.dumps(packet, indent=2, sort_keys=True) + "\n",
    )


def generate_hypotheses(root: Path | str) -> dict[str, Any]:
    """Seed hypothesis packets from current typology matches. Idempotent
    per origin signature; never duplicates an existing packet."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise HypothesisError("vault is not initialized")
    existing = {
        packet.get("origin", {}).get("signature")
        for _, packet in _load_packets(vault)
    }
    created = 0
    existing_count = 0
    for match in scan_typologies(vault)["matches"]:
        signature = _origin_signature(match)
        if signature in existing:
            existing_count += 1
            continue
        now = datetime.now(UTC)
        packet = {
            "kind": "hypothesis",
            "schema_version": "0.1",
            "id": f"hyp-{generate_ulid()}",
            "title": f"possible {match['typology'].replace('_', ' ')}: {match['summary']}",
            "status": "open",
            "origin": {
                "type": "typology",
                "typology": match["typology"],
                "signature": signature,
                "edge_ids": match["edge_ids"],
            },
            "subject_ids": match["member_ids"],
            "evidence_for": [
                {"record_id": edge_id, "path": f"relationships/{edge_id}.md",
                 "why": f"explaining edge for {match['typology']}"}
                for edge_id in match["edge_ids"][:_EVIDENCE_LIMIT]
            ],
            "evidence_against": [],
            "confidence_bounds": {
                "label": "unverified-lead",
                "note": "no probabilistic scoring in v1; treat as an investigative lead only",
            },
            "falsification_checks": list(_FALSIFICATION),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=_EXPIRY_DAYS)).isoformat(),
            "review_trail": [{"event": "created", "at": now.isoformat(), "note": "seeded from typology scan"}],
            "last_evaluation": None,
        }
        _write_packet(vault, packet)
        existing.add(signature)
        created += 1
    return {"status": "ok", "created": created, "existing": existing_count}


def list_hypotheses(root: Path | str) -> list[dict[str, Any]]:
    """List hypothesis packets, metadata only."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise HypothesisError("vault is not initialized")
    return [
        {
            "id": str(packet["id"]),
            "title": str(packet["title"]),
            "status": str(packet["status"]),
            "origin_typology": str(packet.get("origin", {}).get("typology", "")),
            "expires_at": str(packet.get("expires_at", "")),
        }
        for _, packet in _load_packets(vault)
    ]


def show_hypothesis(root: Path | str, hypothesis_id: str) -> dict[str, Any]:
    """Return one full hypothesis packet."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise HypothesisError("vault is not initialized")
    for _, packet in _load_packets(vault):
        if str(packet.get("id")) == hypothesis_id:
            return packet
    raise HypothesisError(f"hypothesis not found: {hypothesis_id}")


def refresh_hypothesis(root: Path | str, hypothesis_id: str) -> dict[str, Any]:
    """Re-evaluate a packet against the current vault.

    Missing explaining edges refute; passing expiry expires; otherwise the
    packet stays open with a recorded evaluation and trail entry.
    """
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise HypothesisError("vault is not initialized")
    for path, packet in _load_packets(vault):
        if str(packet.get("id")) != hypothesis_id:
            continue
        now = datetime.now(UTC)
        edge_ids = [str(item) for item in packet.get("origin", {}).get("edge_ids", [])]
        present = [
            edge_id for edge_id in edge_ids
            if (vault / "relationships" / f"{edge_id}.md").is_file()
        ]
        missing = [edge_id for edge_id in edge_ids if edge_id not in present]
        expires_at = packet.get("expires_at", "")
        expired = False
        if expires_at:
            try:
                expired = datetime.fromisoformat(str(expires_at)) <= now
            except ValueError:
                expired = False
        previous = str(packet.get("status", "open"))
        if previous in {"refuted", "expired"}:
            status = previous
        elif missing:
            status = "refuted"
        elif expired:
            status = "expired"
        else:
            status = "open"
        packet["status"] = status
        packet["last_evaluation"] = {
            "at": now.isoformat(),
            "evidence_present": present,
            "evidence_missing": missing,
        }
        packet.setdefault("review_trail", []).append(
            {"event": "refreshed", "at": now.isoformat(),
             "note": f"status={status}; missing={len(missing)}"}
        )
        _write_packet(vault, packet)
        return {"status": status, "id": hypothesis_id, "missing_evidence": missing}
    raise HypothesisError(f"hypothesis not found: {hypothesis_id}")
