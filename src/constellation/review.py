"""Explicit, conflict-safe review and candidate promotion."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .frontmatter import parse_frontmatter, render_frontmatter
from .models import Analysis, CandidatePatch, Claim, Classification, Decision, Inquiry, Interaction, Opportunity
from .storage import ConflictError, atomic_write_text, safe_relative_path, sha256_file
from .validation import CanonicalValidationError, validate_canonical_text
from .vault import is_initialized


class PromotionError(RuntimeError):
    pass


def write_candidate(root: Path | str, candidate: CandidatePatch) -> Path:
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise PromotionError("vault is not initialized")
    relative = Path(".constellation/candidates") / f"{candidate.id}.json"
    content = candidate.model_dump_json(indent=2) + "\n"
    return atomic_write_text(vault, relative, content)


def _candidate_files(root: Path) -> list[Path]:
    directory = safe_relative_path(root, ".constellation/candidates")
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and not path.is_symlink() and path.suffix == ".json"),
        key=lambda path: path.name,
    )


def _ingest_candidate_summary(
    root: Path, path: Path, payload: dict[str, object]
) -> dict[str, object]:
    source_id = payload.get("source_id")
    source_hash = payload.get("source_hash")
    if (
        payload.get("schema_version") != "0.1"
        or payload.get("kind") != "ingest_candidate"
        or payload.get("status") != "pending_review"
        or not isinstance(source_id, str)
        or not isinstance(source_hash, str)
        or path.stem != f"ingest-{source_id}"
    ):
        raise PromotionError("ingest candidate packet is invalid")
    target_relative = f"source-items/{source_id}.md"
    target = safe_relative_path(root, target_relative)
    if not target.is_file() or target.is_symlink():
        raise PromotionError("ingest candidate source-item is missing or unsafe")
    text = target.read_text(encoding="utf-8")
    validate_canonical_text(text, target_relative)
    metadata, _ = parse_frontmatter(text)
    if (
        metadata.get("id") != source_id
        or metadata.get("type") != "source-item"
        or metadata.get("source_hash") != source_hash
    ):
        raise PromotionError("ingest candidate does not match its canonical source-item")
    return {
        "id": path.stem,
        "kind": "ingest_candidate",
        "title": f"Ingest review: {metadata['title']}",
        "target_path": target_relative,
        "expected_base_hash": sha256_file(target),
        "promotable": True,
    }


def _claim_candidate_summary(path: Path, payload: dict[str, object]) -> dict[str, object]:
    try:
        claim = Claim.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("claim candidate packet is invalid") from exc
    if path.stem != f"claim-{claim.id}":
        raise PromotionError("claim candidate filename does not match claim id")
    return {
        "id": path.stem,
        "kind": "claim_candidate",
        "title": f"Review claim: {claim.title}",
        "target_path": f"claims/{claim.id}.md",
        "expected_base_hash": None,
        "promotable": True,
    }


def _claim_candidate_content(claim: Claim) -> str:
    body = f"# {claim.title}\n"
    if claim.evidence_excerpt:
        body += f"\n{claim.evidence_excerpt}\n"
    return render_frontmatter(claim.model_dump(mode="json", exclude_none=True), body)


def _interaction_candidate_summary(path: Path, payload: dict[str, object]) -> dict[str, object]:
    try:
        interaction = Interaction.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("interaction candidate packet is invalid") from exc
    if path.stem != f"interaction-{interaction.id}":
        raise PromotionError("interaction candidate filename does not match interaction id")
    return {
        "id": path.stem,
        "kind": "interaction_candidate",
        "title": f"Review interaction: {interaction.title}",
        "target_path": f"interactions/{interaction.id}.md",
        "expected_base_hash": None,
        "promotable": True,
    }


def _interaction_candidate_content(interaction: Interaction) -> str:
    return render_frontmatter(
        interaction.model_dump(mode="json", exclude_none=True),
        f"# {interaction.title}\n\n{interaction.summary}\n",
    )


def _decision_candidate_summary(path: Path, payload: dict[str, object]) -> dict[str, object]:
    try:
        decision = Decision.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("decision candidate packet is invalid") from exc
    if path.stem != f"decision-{decision.id}":
        raise PromotionError("decision candidate filename does not match decision id")
    return {
        "id": path.stem,
        "kind": "decision_candidate",
        "title": f"Review decision: {decision.title}",
        "target_path": f"decisions/{decision.id}.md",
        "expected_base_hash": None,
        "promotable": True,
    }


def _decision_candidate_content(decision: Decision) -> str:
    body = f"# {decision.title}\n\n{decision.decision}\n"
    if decision.rationale:
        body += f"\nRationale: {decision.rationale}\n"
    return render_frontmatter(decision.model_dump(mode="json", exclude_none=True), body)


def _inquiry_candidate_summary(path: Path, payload: dict[str, object]) -> dict[str, object]:
    try:
        inquiry = Inquiry.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("inquiry candidate packet is invalid") from exc
    if path.stem != f"inquiry-{inquiry.id}":
        raise PromotionError("inquiry candidate filename does not match inquiry id")
    return {
        "id": path.stem,
        "kind": "inquiry_candidate",
        "title": f"Review inquiry: {inquiry.title}",
        "target_path": f"inquiries/{inquiry.id}.md",
        "expected_base_hash": None,
        "promotable": True,
    }


def _inquiry_candidate_content(inquiry: Inquiry) -> str:
    lines = [
        f"# {inquiry.title}",
        "",
        f"**Question:** {inquiry.question}",
        "",
    ]
    if inquiry.why_it_matters:
        lines.append(f"**Why it matters:** {inquiry.why_it_matters}")
        lines.append("")
    if inquiry.target_scope:
        lines.append(f"**Scope:** {inquiry.target_scope}")
        lines.append("")
    if inquiry.evidence_needed:
        lines.append(f"**Evidence needed:** {inquiry.evidence_needed}")
        lines.append("")
    body = "\n".join(lines) + "\n"
    return render_frontmatter(inquiry.model_dump(mode="json", exclude_none=True), body)


def _promote_inquiry_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _inquiry_candidate_summary(candidate_path, payload)
    if expected_base_hash is not None:
        raise PromotionError("inquiry candidate must be promoted as a create-only record")
    try:
        inquiry = Inquiry.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("inquiry candidate packet is invalid") from exc
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if target.exists():
        raise PromotionError("inquiry target already exists")
    content = _inquiry_candidate_content(inquiry)
    try:
        validate_canonical_text(content, target_path)
        atomic_write_text(root, target_path, content)
    except (CanonicalValidationError, ConflictError) as exc:
        raise PromotionError("inquiry candidate promotion failed") from exc
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "promoted", "target_path": target_path},
    )


def _opportunity_candidate_summary(path: Path, payload: dict[str, object]) -> dict[str, object]:
    try:
        opportunity = Opportunity.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("opportunity candidate packet is invalid") from exc
    if path.stem != f"opportunity-{opportunity.id}":
        raise PromotionError("opportunity candidate filename does not match opportunity id")
    return {
        "id": path.stem,
        "kind": "opportunity_candidate",
        "title": f"Review opportunity: {opportunity.title}",
        "target_path": f"opportunities/{opportunity.id}.md",
        "expected_base_hash": None,
        "promotable": True,
    }


def _opportunity_candidate_content(opportunity: Opportunity) -> str:
    lines = [f"# {opportunity.title}", ""]
    if opportunity.next_action:
        lines.append(f"**Next action:** {opportunity.next_action}")
        lines.append("")
    if opportunity.expected_value:
        lines.append(f"**Expected value:** {opportunity.expected_value}")
        lines.append("")
    if opportunity.probability is not None:
        lines.append(f"**Probability:** {opportunity.probability:.0%}")
        lines.append("")
    lines.append(f"**Stage:** {opportunity.stage.value}")
    body = "\n".join(lines) + "\n"
    return render_frontmatter(opportunity.model_dump(mode="json", exclude_none=True), body)


def _promote_opportunity_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _opportunity_candidate_summary(candidate_path, payload)
    if expected_base_hash is not None:
        raise PromotionError("opportunity candidate must be promoted as a create-only record")
    try:
        opportunity = Opportunity.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("opportunity candidate packet is invalid") from exc
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if target.exists():
        raise PromotionError("opportunity target already exists")
    content = _opportunity_candidate_content(opportunity)
    try:
        validate_canonical_text(content, target_path)
        atomic_write_text(root, target_path, content)
    except (CanonicalValidationError, ConflictError) as exc:
        raise PromotionError("opportunity candidate promotion failed") from exc
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "promoted", "target_path": target_path},
    )


def _classification_candidate_summary(path: Path, payload: dict[str, object]) -> dict[str, object]:
    try:
        classification = Classification.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("classification candidate packet is invalid") from exc
    if path.stem != f"classification-{classification.id}":
        raise PromotionError("classification candidate filename does not match classification id")
    return {
        "id": path.stem,
        "kind": "classification_candidate",
        "title": f"Review classification: {classification.title}",
        "target_path": f"classifications/{classification.id}.md",
        "expected_base_hash": None,
        "promotable": True,
    }


def _classification_candidate_content(classification: Classification) -> str:
    lines = [f"# {classification.title}", ""]
    lines.append(f"**Category:** {classification.category}")
    lines.append(f"**Entity:** {classification.entity_id}")
    lines.append(f"**Confidence:** {classification.confidence}")
    lines.append(f"**Methodology:** {classification.methodology}")
    lines.append("")
    lines.append(f"**Rationale:** {classification.rationale}")
    lines.append("")
    if classification.operator_reviewed:
        lines.append("**Operator reviewed:** Yes")
    else:
        lines.append("**Operator reviewed:** No")
    body = "\n".join(lines) + "\n"
    return render_frontmatter(classification.model_dump(mode="json", exclude_none=True), body)


def _promote_classification_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _classification_candidate_summary(candidate_path, payload)
    if expected_base_hash is not None:
        raise PromotionError("classification candidate must be promoted as a create-only record")
    try:
        classification_obj = Classification.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("classification candidate packet is invalid") from exc
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if target.exists():
        raise PromotionError("classification target already exists")
    content = _classification_candidate_content(classification_obj)
    try:
        validate_canonical_text(content, target_path)
        atomic_write_text(root, target_path, content)
    except (CanonicalValidationError, ConflictError) as exc:
        raise PromotionError("classification candidate promotion failed") from exc
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "promoted", "target_path": target_path},
    )


def list_candidates(root: Path | str) -> list[dict[str, object]]:
    vault = Path(root).absolute()
    results: list[dict[str, object]] = []
    for path in _candidate_files(vault):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            if payload.get("kind") == "ingest_candidate":
                results.append(_ingest_candidate_summary(vault, path, payload))
                continue
            if payload.get("type") == "claim":
                results.append(_claim_candidate_summary(path, payload))
                continue
            if payload.get("type") == "interaction":
                results.append(_interaction_candidate_summary(path, payload))
                continue
            if payload.get("type") == "decision":
                results.append(_decision_candidate_summary(path, payload))
                continue
            if payload.get("type") == "inquiry":
                results.append(_inquiry_candidate_summary(path, payload))
                continue
            if payload.get("type") == "opportunity":
                results.append(_opportunity_candidate_summary(path, payload))
                continue
            if payload.get("type") == "classification":
                results.append(_classification_candidate_summary(path, payload))
                continue
            if payload.get("kind") == "analysis_candidate":
                results.append(_analysis_candidate_summary(path, payload))
                continue
            if payload.get("type") == "analysis":
                results.append(_analysis_candidate_summary(path, payload))
                continue
            if payload.get("type") == "watchlist":
                results.append(_generic_candidate_summary(path, payload, "watchlists"))
                continue
            if payload.get("type") == "snapshot":
                results.append(_generic_candidate_summary(path, payload, "snapshots"))
                continue
            if payload.get("type") == "observation":
                results.append(_generic_candidate_summary(path, payload, "observations"))
                continue
            if payload.get("type") == "event":
                results.append(_generic_candidate_summary(path, payload, "events"))
                continue
            candidate = CandidatePatch.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValidationError, ValueError, PromotionError):
            continue
        results.append(
            {
                "id": candidate.id,
                "kind": "candidate_patch",
                "title": candidate.title,
                "target_path": candidate.target_path,
                "expected_base_hash": candidate.expected_base_hash,
                "promotable": True,
            }
        )
    return results


def _load_candidate(root: Path, candidate_id: str) -> tuple[CandidatePatch, Path]:
    path = safe_relative_path(root, Path(".constellation/candidates") / f"{candidate_id}.json")
    if not path.is_file() or path.is_symlink():
        raise PromotionError("candidate does not exist")
    try:
        return CandidatePatch.model_validate_json(path.read_text(encoding="utf-8")), path
    except (ValidationError, ValueError) as exc:
        raise PromotionError("candidate packet is invalid") from exc


def _append_action(root: Path, event: dict[str, object]) -> None:
    ledger = safe_relative_path(root, ".constellation/action-ledger.jsonl")
    payload = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    descriptor = os.open(ledger, flags, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rebuild_index_after_write(root: Path, result: dict[str, str]) -> dict[str, str]:
    from .retrieval import build_index

    report = build_index(root)
    result["index_generation"] = str(report["generation"])
    return result


def _review_ingest_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _ingest_candidate_summary(root, candidate_path, payload)
    reviewed_hash = summary["expected_base_hash"]
    if expected_base_hash is None or reviewed_hash != expected_base_hash:
        raise PromotionError("base hash conflict")
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if sha256_file(target) != expected_base_hash:
        raise PromotionError("base hash conflict")
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "ingest_candidate_reviewed",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": expected_base_hash,
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "reviewed", "target_path": target_path},
    )


def _promote_claim_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _claim_candidate_summary(candidate_path, payload)
    if expected_base_hash is not None:
        raise PromotionError("claim candidate must be promoted as a create-only record")
    try:
        claim = Claim.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("claim candidate packet is invalid") from exc
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if target.exists():
        raise PromotionError("claim target already exists")
    content = _claim_candidate_content(claim)
    try:
        validate_canonical_text(content, target_path)
        atomic_write_text(root, target_path, content)
    except (CanonicalValidationError, ConflictError) as exc:
        raise PromotionError("claim candidate promotion failed") from exc
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "promoted", "target_path": target_path},
    )


def _promote_interaction_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _interaction_candidate_summary(candidate_path, payload)
    if expected_base_hash is not None:
        raise PromotionError("interaction candidate must be promoted as a create-only record")
    try:
        interaction = Interaction.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("interaction candidate packet is invalid") from exc
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if target.exists():
        raise PromotionError("interaction target already exists")
    content = _interaction_candidate_content(interaction)
    try:
        validate_canonical_text(content, target_path)
        atomic_write_text(root, target_path, content)
    except (CanonicalValidationError, ConflictError) as exc:
        raise PromotionError("interaction candidate promotion failed") from exc
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "promoted", "target_path": target_path},
    )


def _promote_decision_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _decision_candidate_summary(candidate_path, payload)
    if expected_base_hash is not None:
        raise PromotionError("decision candidate must be promoted as a create-only record")
    try:
        decision = Decision.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise PromotionError("decision candidate packet is invalid") from exc
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if target.exists():
        raise PromotionError("decision target already exists")
    content = _decision_candidate_content(decision)
    try:
        validate_canonical_text(content, target_path)
        atomic_write_text(root, target_path, content)
    except (CanonicalValidationError, ConflictError) as exc:
        raise PromotionError("decision candidate promotion failed") from exc
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "promoted", "target_path": target_path},
    )


def promote_candidate(
    root: Path | str,
    candidate_id: str,
    *,
    confirm: bool,
    expected_base_hash: str | None,
) -> dict[str, str]:
    if not confirm:
        raise PromotionError("explicit confirmation is required")
    vault = Path(root).absolute()
    candidate_path = safe_relative_path(
        vault, Path(".constellation/candidates") / f"{candidate_id}.json"
    )
    if not candidate_path.is_file() or candidate_path.is_symlink():
        raise PromotionError("candidate does not exist")
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError("candidate packet is invalid") from exc
    if isinstance(payload, dict) and payload.get("kind") == "ingest_candidate":
        return _review_ingest_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("type") == "claim":
        return _promote_claim_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("type") == "interaction":
        return _promote_interaction_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("type") == "decision":
        return _promote_decision_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("type") == "inquiry":
        return _promote_inquiry_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("type") == "opportunity":
        return _promote_opportunity_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("type") == "classification":
        return _promote_classification_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("kind") == "analysis_candidate":
        return _promote_analysis_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("type") == "analysis":
        return _promote_analysis_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("type") in ("watchlist", "snapshot", "observation", "event"):
        return _promote_generic_candidate(vault, candidate_path, payload, expected_base_hash)
    if isinstance(payload, dict) and payload.get("kind") == "conference-encounter":
        raise PromotionError(
            "conference encounter candidates cannot be auto-promoted — "
            "Aiko hand-writes the entity + outreach draft (see conference-card-intake skill). "
            "The encounter bytes + OCR + PM shell are already staged by lead capture."
        )
    candidate, candidate_path = _load_candidate(vault, candidate_id)
    if candidate.expected_base_hash != expected_base_hash:
        raise PromotionError("expected base hash does not match candidate review")
    try:
        validate_canonical_text(candidate.content, candidate.target_path)
        target = safe_relative_path(vault, candidate.target_path)
    except (CanonicalValidationError, Exception) as exc:
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError("candidate target or canonical schema is invalid") from exc
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise PromotionError("canonical target is unsafe")
        if expected_base_hash is None or sha256_file(target) != expected_base_hash:
            raise PromotionError("base hash conflict")
    elif expected_base_hash is not None:
        raise PromotionError("base hash conflict")
    try:
        atomic_write_text(vault, candidate.target_path, candidate.content, expected_hash=expected_base_hash)
    except ConflictError as exc:
        raise PromotionError("base hash conflict") from exc
    _append_action(
        vault,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate.id,
            "target_path": candidate.target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        vault,
        {"schema_version": "0.1", "status": "promoted", "target_path": candidate.target_path},
    )


_ANALYSIS_EVIDENCE_STATUSES = frozenset({"evidence_available", "insufficient_evidence"})


def _load_analysis_candidate(
    payload: dict[str, object],
) -> tuple[Analysis, str | None, bool]:
    """Return analysis, narrative body, and whether this is a legacy raw packet."""
    legacy = payload.get("kind") is None and payload.get("type") == "analysis"
    if legacy:
        raw_analysis: object = payload
        body_markdown: str | None = None
    else:
        if payload.get("kind") != "analysis_candidate":
            raise PromotionError("analysis candidate envelope kind is invalid")
        raw_analysis = payload.get("analysis")
        body_value = payload.get("body_markdown")
        evidence_status = payload.get("evidence_status")
        if not isinstance(body_value, str) or not body_value.strip():
            raise PromotionError("analysis candidate narrative body is invalid")
        if not isinstance(evidence_status, str) or evidence_status not in _ANALYSIS_EVIDENCE_STATUSES:
            raise PromotionError("analysis candidate evidence status is invalid")
        body_markdown = body_value
    if not isinstance(raw_analysis, dict):
        raise PromotionError("analysis candidate packet is invalid")
    try:
        analysis = Analysis.model_validate_json(json.dumps(raw_analysis))
    except ValidationError as exc:
        raise PromotionError("analysis candidate packet is invalid") from exc
    return analysis, body_markdown, legacy


def _analysis_candidate_summary(path: Path, payload: dict[str, object]) -> dict[str, object]:
    analysis, _, legacy = _load_analysis_candidate(payload)
    title_prefix = "Review legacy analysis (narrative missing)" if legacy else "Review analysis"
    return {
        "id": path.stem,
        "kind": "analysis_candidate",
        "title": f"{title_prefix}: {analysis.title}",
        "target_path": f"analyses/{analysis.id}.md",
        "expected_base_hash": None,
        "promotable": True,
    }


def _analysis_candidate_content(analysis: Analysis, body_markdown: str | None) -> str:
    if body_markdown is not None:
        return render_frontmatter(analysis.model_dump(mode="json", exclude_none=True), body_markdown)
    lines = [
        f"# {analysis.title}",
        "",
        "_Legacy Analysis candidate: no narrative body was staged._",
        "",
    ]
    lines.append(f"**Framework:** {analysis.framework}")
    lines.append(f"**Entity:** {analysis.entity_id}")
    lines.append(f"**Confidence:** {analysis.confidence}")
    if analysis.supporting_claims:
        lines.append(f"**Supporting claims:** {', '.join(analysis.supporting_claims[:10])}")
    if analysis.research_inquiries_spawned:
        lines.append(f"**Research inquiries:** {', '.join(analysis.research_inquiries_spawned[:10])}")
    if analysis.operator_reviewed:
        lines.append("**Operator reviewed:** Yes")
    else:
        lines.append("**Operator reviewed:** No — review required")
    body = "\n".join(lines) + "\n"
    return render_frontmatter(analysis.model_dump(mode="json", exclude_none=True), body)


def _promote_analysis_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _analysis_candidate_summary(candidate_path, payload)
    if expected_base_hash is not None:
        raise PromotionError("analysis candidate must be promoted as a create-only record")
    analysis_obj, body_markdown, _ = _load_analysis_candidate(payload)
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if target.exists():
        raise PromotionError("analysis target already exists")
    content = _analysis_candidate_content(analysis_obj, body_markdown)
    try:
        validate_canonical_text(content, target_path)
        atomic_write_text(root, target_path, content)
    except (CanonicalValidationError, ConflictError) as exc:
        raise PromotionError("analysis candidate promotion failed") from exc
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "promoted", "target_path": target_path},
    )


def _generic_candidate_summary(path: Path, payload: dict[str, object], folder: str) -> dict[str, object]:
    record_id = str(payload.get("id", ""))
    title = str(payload.get("title", path.stem))
    return {
        "id": path.stem,
        "kind": f"{folder}_candidate",
        "title": f"Review {folder}: {title}",
        "target_path": f"{folder}/{record_id}.md",
        "expected_base_hash": None,
        "promotable": True,
    }


def _promote_generic_candidate(
    root: Path,
    candidate_path: Path,
    payload: dict[str, object],
    expected_base_hash: str | None,
) -> dict[str, str]:
    summary = _generic_candidate_summary(candidate_path, payload, str(payload.get("type", "unknown")))
    if expected_base_hash is not None:
        raise PromotionError("generic candidate must be promoted as a create-only record")
    target_path = str(summary["target_path"])
    target = safe_relative_path(root, target_path)
    if target.exists():
        raise PromotionError("generic target already exists")
    content = render_frontmatter(
        {k: v for k, v in payload.items()},
        str(payload.get("title", "")),
    )
    try:
        validate_canonical_text(content, target_path)
        atomic_write_text(root, target_path, content)
    except (CanonicalValidationError, ConflictError) as exc:
        raise PromotionError("generic candidate promotion failed") from exc
    _append_action(
        root,
        {
            "schema_version": "0.1",
            "action": "candidate_promoted",
            "candidate_id": candidate_path.stem,
            "target_path": target_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "result_hash": sha256_file(target),
        },
    )
    candidate_path.unlink()
    return _rebuild_index_after_write(
        root,
        {"schema_version": "0.1", "status": "promoted", "target_path": target_path},
    )
