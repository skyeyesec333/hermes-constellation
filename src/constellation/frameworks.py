"""Strategic framework execution — Porter Five Forces and SWOT.

Each framework gathers vault evidence, formats a structured analysis, and
stages a review-only Analysis candidate.  Frameworks produce inference, not
source fact — insufficient evidence is flagged, not filled with prose.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .frontmatter import parse_frontmatter
from .models import Analysis, Claim, EntityRecord, generate_ulid
from .storage import atomic_write_text
from .identity import SubjectResolutionError, resolve_subject
from .validation import validate_canonical_text
from .vault import is_initialized

_VALID_FRAMEWORKS = frozenset({"porter_five_forces", "swot"})


class FrameworkError(RuntimeError):
    """Raised when a framework run fails."""


# ── Entity resolution ──────────────────────────────────────────────────


def _require_entity(vault: Path, entity_id: str) -> EntityRecord:
    try:
        return resolve_subject(vault, entity_id).record
    except SubjectResolutionError as exc:
        if "not found" in str(exc):
            raise FrameworkError(f"entity not found: {entity_id}") from exc
        raise FrameworkError(f"entity is invalid: {entity_id}: {exc}") from exc


# ── Evidence gathering ─────────────────────────────────────────────────


def _gather_evidence(vault: Path, entity_id: str) -> list[dict[str, object]]:
    """Load only validated canonical Claims whose subject matches the entity."""
    evidence: list[dict[str, object]] = []
    claims_directory = vault / "claims"
    if not claims_directory.is_dir() or claims_directory.is_symlink():
        return evidence
    for path in sorted(claims_directory.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            validate_canonical_text(text, path.relative_to(vault).as_posix())
            metadata, body = parse_frontmatter(text)
            claim = Claim.model_validate(metadata, strict=False)
        except Exception:
            continue
        if claim.subject_id != entity_id:
            continue
        evidence.append(
            {
                "id": claim.id,
                "title": claim.title,
                "type": claim.type,
                "path": path.relative_to(vault).as_posix(),
                "snippet": body.strip()[:120],
            }
        )
    return evidence


# ── Framework-specific analysis ─────────────────────────────────────────


def _porter_body(entity: EntityRecord, evidence: list[dict[str, object]]) -> str:
    """Build Porter Five Forces analysis body."""
    entity_name = entity.title
    claim_refs = _collect_claim_refs(evidence)

    lines = [
        f"# Porter's Five Forces: {entity_name}",
        "",
        "## Overall Assessment",
        "",
        "_[Review required — operator must assess industry attractiveness based on evidence below.]_",
        "",
        "## 1. Threat of New Entrants: Insufficient Evidence",
        "",
        "**Evidence available:**",
    ]
    if claim_refs:
        for ref in claim_refs[:3]:
            lines.append(f"- {ref}")
    else:
        lines.append("- No direct claims in vault.")
    lines.extend([
        "",
        "**Gaps:** Barriers to entry, capital requirements, regulatory hurdles, recent entrants.",
        "",
        "## 2. Bargaining Power of Suppliers: Insufficient Evidence",
        "",
        "**Evidence available:**",
    ])
    if len(claim_refs) > 3:
        for ref in claim_refs[3:6]:
            lines.append(f"- {ref}")
    else:
        lines.append("- No direct claims in vault.")
    lines.extend([
        "",
        "**Gaps:** Supplier concentration, switching costs, forward integration risk.",
        "",
        "## 3. Bargaining Power of Buyers: Insufficient Evidence",
        "",
        "**Evidence available:**",
        "- No direct claims in vault.",
        "",
        "**Gaps:** Customer concentration, switching costs, backward integration risk.",
        "",
        "## 4. Threat of Substitutes: Insufficient Evidence",
        "",
        "**Evidence available:**",
        "- No direct claims in vault.",
        "",
        "**Gaps:** Alternative products, price-performance tradeoffs, switching likelihood.",
        "",
        "## 5. Industry Rivalry: Insufficient Evidence",
        "",
        "**Evidence available:**",
    ])
    if len(claim_refs) > 6:
        for ref in claim_refs[6:9]:
            lines.append(f"- {ref}")
    else:
        lines.append("- No direct claims in vault.")
    lines.extend([
        "",
        "**Gaps:** Competitor count, industry growth rate, exit barriers, competitive intensity.",
        "",
        "## Sources",
        "",
        f"- Entity: {entity.id} ({entity_name})",
        f"- Vault claims reviewed: {len(claim_refs)}",
        "",
        "_Operator review required before this analysis becomes canonical._",
        "",
    ])
    return "\n".join(lines)


def _swot_body(entity: EntityRecord, evidence: list[dict[str, object]]) -> str:
    """Build SWOT analysis body."""
    entity_name = entity.title
    claim_refs = _collect_claim_refs(evidence)

    lines = [
        f"# SWOT Analysis: {entity_name}",
        "",
        "## Strengths (Internal)",
        "",
    ]
    if claim_refs:
        for ref in claim_refs[:4]:
            lines.append(f"- {ref}")
    else:
        lines.append("- No direct claims in vault.")
    lines.extend([
        "",
        "## Weaknesses (Internal)",
        "",
        "- No direct claims in vault.",
        "",
        "## Opportunities (External)",
        "",
        "- No direct claims in vault.",
        "",
        "## Threats (External)",
        "",
        "- No direct claims in vault.",
        "",
        "## Cross-Impact",
        "",
        "_[Review required — which strengths address which threats?]_",
        "",
        "## Strategic Implications",
        "",
        "_[Review required — what should the entity do differently?]_",
        "",
        "## Sources",
        "",
        f"- Entity: {entity.id} ({entity_name})",
        f"- Vault claims reviewed: {len(claim_refs)}",
        "",
        "_Operator review required before this analysis becomes canonical._",
        "",
    ])
    return "\n".join(lines)


def _collect_claim_refs(evidence: list[dict[str, object]]) -> list[str]:
    refs: list[str] = []
    for item in evidence:
        claim_id = str(item.get("id", ""))
        title = str(item.get("title", ""))
        snippet = str(item.get("snippet", ""))[:120]
        if claim_id:
            refs.append(f"{claim_id}: {title} — \"{snippet}\"")
    return refs


# ── Candidate staging ──────────────────────────────────────────────────


def _stage_analysis(
    vault: Path,
    *,
    framework: str,
    entity: EntityRecord,
    body: str,
    claim_refs: list[str],
) -> dict[str, object]:
    now = datetime.now(UTC)
    analysis = Analysis(
        id=generate_ulid(),
        title=f"{framework.replace('_', ' ').title()}: {entity.title}",
        status="active",
        sensitivity=entity.sensitivity,
        created_at=now,
        updated_at=now,
        framework=framework,
        entity_id=entity.id,
        supporting_claims=[
            ref.split(":")[0] for ref in claim_refs if ":" in ref
        ],
        research_inquiries_spawned=[],
        confidence="medium" if claim_refs else "low",
        operator_reviewed=False,
    )
    candidate_rel = Path(".constellation/candidates") / f"analysis-{analysis.id}.json"
    candidate = {
        "kind": "analysis_candidate",
        "analysis": analysis.model_dump(mode="json", exclude_none=True),
        "body_markdown": body,
        "evidence_status": "evidence_available" if claim_refs else "insufficient_evidence",
    }
    from json import dumps as _json_dumps

    atomic_write_text(vault, candidate_rel, _json_dumps(candidate, indent=2, sort_keys=True) + "\n")
    return {
        "status": "staged",
        "analysis_id": analysis.id,
        "framework": framework,
        "entity_id": entity.id,
        "candidate_path": candidate_rel.as_posix(),
        "evidence_count": len(claim_refs),
    }


# ── Public API ──────────────────────────────────────────────────────────


def run_framework(
    vault: Path | str,
    entity_id: str,
    framework: str,
) -> dict[str, object]:
    """Run a strategic framework and stage its Analysis candidate.

    Args:
        vault: Constellation vault path.
        entity_id: Canonical entity ULID.
        framework: "porter_five_forces" or "swot".

    Returns:
        Dict with status, analysis_id, candidate_path, evidence_count.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise FrameworkError("vault is not initialized")
    if framework not in _VALID_FRAMEWORKS:
        raise FrameworkError(f"unsupported framework: {framework}")

    entity = _require_entity(vault, entity_id)
    evidence = _gather_evidence(vault, entity.id)

    if framework == "porter_five_forces":
        body = _porter_body(entity, evidence)
    else:
        body = _swot_body(entity, evidence)

    claim_refs = _collect_claim_refs(evidence)
    return _stage_analysis(vault, framework=framework, entity=entity, body=body, claim_refs=claim_refs)
