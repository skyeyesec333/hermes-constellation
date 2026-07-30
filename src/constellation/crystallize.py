"""Stage 7.4 — crystallization: session/work artifacts -> review-gated digests.

A work artifact (session notes, maintenance log, research summary) is
distilled DETERMINISTICALLY into a structured digest: headings and bullet
items are extracted, the digest is preserved with its own SHA-256, and the
whole thing is staged as a review-only candidate_patch targeting
source-items/ — the normal review gate, nothing auto-promotes.

Provenance is cited end to end: the digest body carries the source
artifact's vault-relative path, its SHA-256, the actor, and the timestamp;
the staged frontmatter's source_hash is the hash of the preserved digest
bytes. Every run is journaled (.constellation/crystallizations.jsonl).
Reruns over the same artifact are idempotent (digest id is hash-derived).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import render_frontmatter
from .ingest import _id_from_hash
from .models import CandidatePatch, Sensitivity
from .review import write_candidate
from .storage import atomic_write_text, sha256_bytes, sha256_file
from .vault import is_initialized

_MIN_ITEM_CHARS = 20
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_JOURNAL_REL = Path(".constellation/crystallizations.jsonl")


class CrystallizeError(RuntimeError):
    """Raised when crystallization cannot proceed truthfully."""


def _parse_structure(text: str) -> list[dict[str, Any]]:
    """Extract (section, items) structure from a markdown artifact."""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading and len(heading.group(1)) >= 2:  # H1 is the doc title, not a section
            current = {"heading": heading.group(2).strip(), "items": []}
            sections.append(current)
            continue
        bullet = _BULLET.match(line)
        if bullet:
            item = bullet.group(1).strip()
            if len(item) >= _MIN_ITEM_CHARS:
                if current is None:
                    current = {"heading": "(untitled)", "items": []}
                    sections.append(current)
                current["items"].append(item)
    return sections


def crystallize_artifact(
    vault: Path | str,
    artifact: Path | str,
    *,
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Distill one vault artifact into a staged, provenance-cited digest."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise CrystallizeError("vault is not initialized")
    if not actor.strip():
        raise CrystallizeError("actor is required (who ran the crystallization)")

    artifact_path = Path(artifact)
    if artifact_path.is_absolute():
        try:
            relative = artifact_path.relative_to(vault)
        except ValueError as exc:
            raise CrystallizeError("artifact must be inside the vault root") from exc
    else:
        relative = artifact_path
    source_path = vault / relative
    if source_path.is_symlink() or not source_path.is_file():
        raise CrystallizeError(f"artifact not found: {relative}")

    text = source_path.read_text(encoding="utf-8")
    artifact_sha = sha256_file(source_path)
    sections = _parse_structure(text)
    if not sections:
        raise CrystallizeError(
            f"artifact has no extractable structure (headings + bullets >= {_MIN_ITEM_CHARS} chars)"
        )
    instant = now or datetime.now(UTC)
    item_count = sum(len(s["items"]) for s in sections)
    if item_count == 0:
        raise CrystallizeError(
            f"artifact has no extractable structure (headings + bullets >= {_MIN_ITEM_CHARS} chars)"
        )

    lines = [
        f"# Crystallized digest: {source_path.stem}",
        "",
        "## Provenance",
        "",
        f"- source artifact: {relative.as_posix()}",
        f"- source sha256: {artifact_sha}",
        f"- crystallized by: {actor}",
        f"- crystallized at: {instant.isoformat()}",
        f"- sections: {len(sections)}; items: {item_count}",
        "",
        "This digest is a deterministic distillation staged for review.",
        "Promotion follows the normal candidate gate; nothing auto-promotes.",
        "",
    ]
    for section in sections:
        lines.append(f"## {section['heading']}")
        lines.append("")
        for item in section["items"]:
            lines.append(f"- {item}")
        lines.append("")
    digest_text = "\n".join(lines)
    digest_sha = sha256_bytes(digest_text.encode("utf-8"))
    # one digest per artifact VERSION: id derives from the artifact hash, so
    # reruns are idempotent and any artifact edit yields a fresh digest.
    digest_id = _id_from_hash(artifact_sha)

    candidate_rel = Path(".constellation/candidates") / f"{digest_id}.json"
    if (vault / candidate_rel).is_file():
        return {
            "status": "already_staged",
            "candidate_id": digest_id,
            "artifact_sha256": artifact_sha,
            "sections": len(sections),
            "items": item_count,
        }

    preserved_rel = Path("Library/Text") / f"crystallized-{digest_id}.md"
    atomic_write_text(vault, preserved_rel, digest_text)

    from .models import SourceItem

    item = SourceItem(
        id=digest_id,
        type="source-item",
        title=f"Crystallized digest: {source_path.stem}",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        created_at=instant,
        updated_at=instant,
        source_hash=digest_sha,
        original_path=preserved_rel.as_posix(),
        extracted_text_path=preserved_rel.as_posix(),
        extraction_status="complete",
        media_type="text/markdown",
    )
    body = f"# {item.title}\n\n{digest_text}\n"
    candidate = CandidatePatch(
        id=digest_id,
        type="candidate-patch",
        title=f"Crystallized digest: {source_path.name}",
        status="pending-review",
        sensitivity=Sensitivity.INTERNAL,
        created_at=instant,
        updated_at=instant,
        target_path=f"source-items/{digest_id}.md",
        content=render_frontmatter(item.model_dump(mode="json", exclude_none=True), body),
        expected_base_hash=None,
    )
    write_candidate(vault, candidate)

    journal = vault / _JOURNAL_REL
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "timestamp": instant.isoformat(),
            "actor": actor,
            "artifact": relative.as_posix(),
            "artifact_sha256": artifact_sha,
            "digest_id": digest_id,
            "digest_sha256": digest_sha,
            "candidate_id": digest_id,
            "sections": len(sections),
            "items": item_count,
        }, sort_keys=True) + "\n")

    return {
        "status": "staged",
        "candidate_id": digest_id,
        "digest_path": preserved_rel.as_posix(),
        "artifact_sha256": artifact_sha,
        "sections": len(sections),
        "items": item_count,
    }
