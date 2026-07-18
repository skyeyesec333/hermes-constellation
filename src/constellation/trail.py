"""Decision trail — full provenance chain from decision back to evidence.

Phase 11: constellation trail <decision-id> returns the complete chain:
decision → interactions → claims → evidence sources.
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import parse_frontmatter
from .vault import is_initialized


class TrailError(RuntimeError):
    """Raised when a provenance chain cannot be traversed."""


def _find_record(vault: Path, folder: str, record_id: str) -> dict[str, object] | None:
    """Find a canonical record by ID in a folder, searching frontmatter."""
    base = vault / folder
    if not base.is_dir():
        return None
    for path in sorted(base.glob("*.md")):
        try:
            fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            if not isinstance(fm, dict):
                continue
            if str(fm.get("id", "")) == record_id:
                return {
                    "id": record_id,
                    "title": str(fm.get("title", "")),
                    "type": str(fm.get("type", "")),
                    "status": str(fm.get("status", "")),
                    "body": str(body).strip(),
                    "raw": fm,
                }
        except Exception:
            continue
    return None


def _find_claims_for_entity(vault: Path, entity_id: str) -> list[dict[str, object]]:
    """Find all claims with a given subject_id."""
    results: list[dict[str, object]] = []
    base = vault / "claims"
    if not base.is_dir():
        return results
    for path in sorted(base.glob("*.md")):
        try:
            fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            if not isinstance(fm, dict):
                continue
            if str(fm.get("subject_id", "")) == entity_id:
                results.append({
                    "id": str(fm.get("id", "")),
                    "title": str(fm.get("title", "")),
                    "predicate": str(fm.get("predicate", "")),
                    "object_literal": str(fm.get("object_literal", "")),
                    "source_ids": [str(s) for s in (fm.get("source_ids") or [])],
                    "evidence_excerpt": str(fm.get("evidence_excerpt", "")),
                    "observed_at": str(fm.get("observed_at", "")),
                })
        except Exception:
            continue
    return results


def trace_decision(root: Path | str, decision_id: str) -> str:
    """Walk the full provenance chain for a decision.

    Returns a markdown trail: decision → interactions → claims → evidence sources.
    """
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise TrailError("vault is not initialized")

    decision = _find_record(vault, "decisions", decision_id)
    if decision is None:
        raise TrailError(f"decision not found: {decision_id}")

    lines: list[str] = []
    dm = decision["raw"]

    lines.append(f"# Decision Trail: {decision['title']}")
    lines.append("")
    lines.append(f"**Decision:** {dm.get('decision', '')}  ")
    if dm.get("rationale"):
        lines.append(f"**Rationale:** {dm['rationale']}  ")
    if dm.get("decided_at"):
        lines.append(f"**Decided:** {dm['decided_at']}  ")
    if dm.get("owner"):
        lines.append(f"**Owner:** {dm['owner']}  ")
    lines.append("")

    # ── Linked interactions ──
    interaction_ids = [str(s) for s in (dm.get("source_ids") or [])]
    interactions_found = []
    for iid in interaction_ids:
        ix = _find_record(vault, "interactions", iid)
        if ix:
            interactions_found.append(ix)

    if interactions_found:
        lines.append("## Supporting Interactions")
        lines.append("")
        for ix in interactions_found:
            im = ix["raw"]
            lines.append(f"### {ix['title']}")
            if im.get("occurred_at"):
                lines.append(f"**When:** {im['occurred_at']}  ")
            if im.get("summary"):
                lines.append(f"{im['summary']}  ")
            lines.append("")

    # ── Supporting claims ──
    claim_ids = [str(s) for s in (dm.get("supporting_claims") or [])]
    claims_found = []
    for cid in claim_ids:
        claim = _find_record(vault, "claims", cid)
        if claim:
            claims_found.append(claim)

    if claims_found:
        lines.append("## Supporting Claims")
        lines.append("")
        for claim in claims_found:
            lines.append(f"- **{claim['predicate']}** — {claim['object_literal']}")
            if claim.get("evidence_excerpt"):
                lines.append(f"  > {claim['evidence_excerpt']}")
            lines.append("")

    # ── Evidence sources ──
    seen_sources: set[str] = set()
    for claim in claims_found:
        for sid in claim.get("source_ids", []):
            if sid not in seen_sources:
                seen_sources.add(sid)

    if seen_sources:
        lines.append("## Evidence Sources")
        lines.append("")
        for sid in sorted(seen_sources):
            src = _find_record(vault, "source-items", sid)
            if src:
                lines.append(f"- **{src['title']}** (`{sid}`)")
            else:
                lines.append(f"- `{sid}` (source item not in vault)")
        lines.append("")

    # ── What changed? ──
    contradictory = [str(s) for s in (dm.get("supporting_claims") or [])]
    # Check if any claims have been superseded
    superseded = []
    for cid in contradictory:
        claim = _find_record(vault, "claims", cid)
        if claim and claim["raw"].get("superseded_by"):
            superseded.append(cid)

    if superseded:
        lines.append("## ⚠️ Superseded Claims")
        lines.append("")
        for cid in superseded:
            lines.append(f"- `{cid}` has been superseded — this decision may need review.")
        lines.append("")

    if not interactions_found and not claims_found:
        lines.append("*No supporting interactions or claims found — this decision is unanchored.*")
        lines.append("")

    return "\n".join(lines) + "\n"
