"""Bounded, deterministic evidence packets for review-only intelligence work."""

from __future__ import annotations

import hashlib
import json

from .retrieval import search


def build_evidence_packet(
    root: str,
    query: str,
    *,
    limit: int = 10,
    max_bytes: int = 32_768,
    sensitivity_ceiling: str = "restricted",
) -> dict[str, object]:
    """Retrieve a small anchored packet; never reads or writes beyond canonical search."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    retrieved = search(root, query, limit=limit, sensitivity_ceiling=sensitivity_ceiling)
    if retrieved["status"] != "evidence_found":
        return {
            "status": "evidence_not_ready",
            "query": query,
            "evidence": [],
            "reason": retrieved.get("reason", retrieved["status"]),
        }
    evidence_raw = retrieved["evidence"]
    if not isinstance(evidence_raw, list):
        raise ValueError("retrieval evidence has an invalid shape")
    evidence = evidence_raw
    packet = {
        "status": "evidence_ready",
        "query": query,
        "sensitivity_ceiling": sensitivity_ceiling,
        "evidence": evidence,
    }
    encoded = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("evidence packet exceeds max_bytes")
    return {**packet, "bytes": len(encoded), "packet_sha256": hashlib.sha256(encoded).hexdigest()}
