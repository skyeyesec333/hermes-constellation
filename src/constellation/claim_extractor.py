"""Auto claim extraction from preserved research sources.

Phase 13: reads preserved source markdown, calls an LLM to extract factual
assertions, deduplicates against existing claims, and stages candidates.
Never auto-promotes — all claims are review-required.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .claim import stage_claim
from .egress import EgressDenied, EgressRequest, require_egress
from .frontmatter import parse_frontmatter
from .models import Sensitivity, SourceItem, generate_ulid
from .storage import atomic_write_text, safe_relative_path
from .identity import SubjectResolutionError, resolve_subject
from .url_safety import UnsafeUrlError, validate_http_url
from .vault import is_initialized


class ClaimExtractionError(RuntimeError):
    """Raised when claim extraction fails."""


_MAX_MODEL_RESPONSE_BYTES = 1_000_000
_MAX_MODEL_TOKENS = 4_000
_CONFIDENCE = {"direct_quote": 0.95, "paraphrase": 0.85, "inference": 0.70}


@dataclass(frozen=True, slots=True)
class _ExtractedClaim:
    predicate: str
    object_literal: str
    evidence_excerpt: str
    evidence_anchor: str
    confidence: float


EXTRACTION_PROMPT = """Extract factual assertions from the following document.
Return a JSON array of claims. Each claim must have:

- predicate: a short verb phrase (e.g. "approved_investment", "works_at", "founded_in")
- object_literal: the factual statement (string)
- evidence_excerpt: exact text from the document that supports this claim (string, max 200 chars)
- confidence: "direct_quote" if the exact text states this, "paraphrase" if clearly implied,
  "inference" if reasonably inferred from context

Rules:
- Only extract claims that are clearly supported by the document text
- Do not invent claims not present in the document
- Do not repeat the same claim with different wording
- Include the exact evidence excerpt, not a summary
- For dates, amounts, and names: use the exact values from the document

Document:
{document}

Return ONLY the JSON array, nothing else."""


def _extraction_input_sha256(source_content: str, source_hash: str) -> str:
    """Stable hash of the extraction input for receipt traceability."""
    packet = json.dumps(
        {"source_hash": source_hash, "content_sha256": hashlib.sha256(source_content.encode()).hexdigest()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(packet.encode()).hexdigest()


def _write_extraction_receipt(
    vault: Path,
    receipt_id: str,
    *,
    status: str,
    provider: str,
    model: str,
    source_ids: list[str],
    source_hash: str,
    input_sha256: str,
    authorization_id: str | None = None,
    provider_request_id: str | None = None,
    response_sha256: str | None = None,
    usage: object = None,
    staged: int = 0,
    skipped: int = 0,
    claim_ids: list[str] | None = None,
    error: str | None = None,
) -> str:
    relative = Path(".constellation/claim-extractions") / f"{receipt_id}.json"
    payload = {
        "schema_version": "0.1",
        "receipt_id": receipt_id,
        "status": status,
        "provider": provider,
        "model": model,
        "source_ids": source_ids,
        "source_hash": source_hash,
        "input_sha256": input_sha256,
        "authorization_id": authorization_id,
        "provider_request_id": provider_request_id,
        "response_sha256": response_sha256,
        "usage": usage,
        "staged": staged,
        "skipped": skipped,
        "claim_ids": claim_ids or [],
        "error": error,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(vault, relative, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return relative.as_posix()


def _exact_line_anchor(source_content: str, excerpt: str) -> str:
    start = source_content.find(excerpt)
    if start < 0:
        raise ClaimExtractionError("claim evidence excerpt was not found exactly in the source")
    start_line = source_content.count("\n", 0, start) + 1
    end_line = start_line + excerpt.count("\n")
    return f"L{start_line:06d}-L{end_line:06d}"


def _parse_claims(raw_content: str, source_content: str) -> list[_ExtractedClaim]:
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ClaimExtractionError(f"failed to parse model claims JSON: {exc}") from exc
    values = payload if isinstance(payload, list) else payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise ClaimExtractionError("model claims must be a JSON array")

    claims: list[_ExtractedClaim] = []
    for value in values:
        if not isinstance(value, dict):
            raise ClaimExtractionError("each model claim must be a JSON object")
        fields = [value.get(name) for name in (
            "predicate",
            "object_literal",
            "evidence_excerpt",
            "confidence",
        )]
        if not all(isinstance(field, str) and field.strip() for field in fields):
            raise ClaimExtractionError("model claim fields must be non-empty strings")
        predicate, object_literal, evidence, confidence_name = fields
        assert isinstance(predicate, str)
        assert isinstance(object_literal, str)
        assert isinstance(evidence, str)
        assert isinstance(confidence_name, str)
        if confidence_name not in _CONFIDENCE:
            raise ClaimExtractionError("model claim confidence is invalid")
        evidence = evidence.strip()
        claims.append(
            _ExtractedClaim(
                predicate=predicate.strip(),
                object_literal=object_literal.strip(),
                evidence_excerpt=evidence,
                evidence_anchor=_exact_line_anchor(source_content, evidence),
                confidence=_CONFIDENCE[confidence_name],
            )
        )
    return claims


def _require_canonical_subject(vault: Path, subject_id: str) -> None:
    try:
        resolve_subject(vault, subject_id)
    except SubjectResolutionError as exc:
        raise ClaimExtractionError(str(exc)) from exc


def _require_matching_source_items(
    vault: Path, source_ids: list[str], source_hash: str
) -> list[SourceItem]:
    if not source_ids:
        raise ClaimExtractionError("at least one canonical source item is required")
    records: list[SourceItem] = []
    for source_id in source_ids:
        path = safe_relative_path(vault, Path("source-items") / f"{source_id}.md")
        if not path.is_file() or path.is_symlink():
            raise ClaimExtractionError(f"canonical source item not found: {source_id}")
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            record = SourceItem.model_validate(metadata, strict=False)
        except Exception as exc:
            raise ClaimExtractionError(f"canonical source item is invalid: {source_id}") from exc
        if record.id != source_id or record.source_hash != source_hash:
            raise ClaimExtractionError(
                f"canonical source item does not match the preserved source hash: {source_id}"
            )
        records.append(record)
    return records


def _source_ids_for_hash(vault: Path, source_hash: str) -> list[str]:
    matches: list[str] = []
    for path in sorted((vault / "source-items").glob("*.md")):
        if path.is_symlink():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            record = SourceItem.model_validate(metadata, strict=False)
        except Exception:
            continue
        if record.source_hash == source_hash:
            matches.append(record.id)
    if not matches:
        raise ClaimExtractionError(
            f"no canonical source item matches research source hash: {source_hash}"
        )
    return matches


def _invoke_model(
    *,
    provider: str,
    model: str,
    prompt: str,
    transport: str | None,
    model_caller: Callable[..., object] | None,
    api_key: str | None,
) -> tuple[str, str | None, object]:
    if model_caller is not None:
        response = model_caller(
            provider=provider,
            model=model,
            prompt=prompt,
            max_tokens=_MAX_MODEL_TOKENS,
        )
        if not isinstance(response, dict):
            raise ClaimExtractionError("model caller returned an invalid response")
        content = response.get("content")
        if not isinstance(content, str) or not content:
            raise ClaimExtractionError("model caller returned empty content")
        request_id = str(response.get("provider_request_id") or "") or None
        return content, request_id, response.get("usage")

    key = api_key or os.environ.get("CONSTELLATION_MODEL_API_KEY", "")
    endpoint = os.environ.get("CONSTELLATION_MODEL_ENDPOINT", "")
    if not endpoint or not key:
        raise ClaimExtractionError(
            "model endpoint and credentials are required; set "
            "CONSTELLATION_MODEL_ENDPOINT and CONSTELLATION_MODEL_API_KEY"
        )
    try:
        endpoint = validate_http_url(endpoint, allow_localhost=transport == "local")
    except UnsafeUrlError as exc:
        raise ClaimExtractionError(f"model endpoint is unsafe: {exc}") from exc
    if transport == "external" and not endpoint.startswith("https://"):
        raise ClaimExtractionError("external model endpoint must use HTTPS")

    payload_data: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": _MAX_MODEL_TOKENS,
    }
    reasoning_setting = os.environ.get("CONSTELLATION_MODEL_REASONING_ENABLED")
    if reasoning_setting is not None:
        normalized_reasoning = reasoning_setting.strip().casefold()
        if normalized_reasoning in {"true", "1", "yes", "on"}:
            reasoning_enabled = True
        elif normalized_reasoning in {"false", "0", "no", "off"}:
            reasoning_enabled = False
        else:
            raise ClaimExtractionError(
                "CONSTELLATION_MODEL_REASONING_ENABLED must be true or false"
            )
        payload_data["reasoning"] = {"enabled": reasoning_enabled}
    payload = json.dumps(payload_data).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_bytes = response.read(_MAX_MODEL_RESPONSE_BYTES + 1)
        if len(response_bytes) > _MAX_MODEL_RESPONSE_BYTES:
            raise ClaimExtractionError("model response exceeds 1000000 bytes")
        body = json.loads(response_bytes.decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ClaimExtractionError(f"model provider is unreachable: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaimExtractionError(f"model provider returned invalid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ClaimExtractionError("model provider returned an invalid response")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ClaimExtractionError("model provider returned no choices")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content:
        raise ClaimExtractionError("model provider returned empty content")
    request_id = str(body.get("id") or "") or None
    return content, request_id, body.get("usage")


def _existing_claim_keys(vault: Path) -> set[tuple[str, str]]:
    """Build a set of (predicate, object_literal) for dedup."""
    keys: set[tuple[str, str]] = set()
    claims_dir = vault / "claims"
    if not claims_dir.is_dir():
        return keys
    for path in claims_dir.glob("*.md"):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            if not isinstance(fm, dict):
                continue
            pred = str(fm.get("predicate", "")).strip().casefold()
            obj = str(fm.get("object_literal", "")).strip().casefold()
            if pred and obj:
                keys.add((pred, obj))
        except Exception:
            continue
    return keys


def extract_claims_from_source(
    vault: Path | str,
    source_path: Path | str,
    *,
    subject_id: str,
    source_ids: list[str],
    provider: str | None = None,
    model: str | None = None,
    model_caller: Callable[..., object] | None = None,
    api_key: str | None = None,
) -> dict[str, object]:
    """Extract claims from a preserved source and stage them as candidates.

    Args:
        vault: Path to the Constellation vault
        source_path: Path to the preserved source markdown file
        subject_id: ULID of the entity the claims are about
        source_ids: ULIDs of source items these claims reference
        provider: Egress-policy provider identifier
        model: Provider model identifier
        api_key: Model endpoint credential (defaults to CONSTELLATION_MODEL_API_KEY)

    Returns:
        Dict with status, staged_count, skipped_count (duplicates), claim_ids
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise ClaimExtractionError("vault is not initialized")

    src = Path(source_path)
    if not src.is_file():
        raise ClaimExtractionError(f"source file not found: {src}")

    source_content = src.read_text(encoding="utf-8")
    if not source_content.strip():
        raise ClaimExtractionError("source file is empty")

    source_hash = hashlib.sha256(source_content.encode()).hexdigest()
    input_sha256 = _extraction_input_sha256(source_content, source_hash)
    provider_name = (provider or "").strip()
    model_name = (model or "").strip()
    if not provider_name or not model_name:
        raise ClaimExtractionError("provider and model are required")

    receipt_id = generate_ulid()
    try:
        _require_canonical_subject(vault, subject_id)
        source_items = _require_matching_source_items(vault, source_ids, source_hash)
        sensitivity_order = tuple(Sensitivity)
        canonical_sensitivity = max(
            (item.sensitivity for item in source_items),
            key=sensitivity_order.index,
        )
    except ClaimExtractionError as exc:
        _write_extraction_receipt(
            vault,
            receipt_id,
            status="failed",
            provider=provider_name,
            model=model_name,
            source_ids=source_ids,
            source_hash=source_hash,
            input_sha256=input_sha256,
            error=str(exc),
        )
        raise

    try:
        authorization = require_egress(
            vault,
            EgressRequest(
                provider=provider_name,
                model=model_name,
                purpose="stage1",
                sensitivity=canonical_sensitivity,
                source_hashes=(source_hash,),
                request_input_sha256=input_sha256,
            ),
        )
    except EgressDenied as exc:
        _write_extraction_receipt(
            vault,
            receipt_id,
            status="denied",
            provider=provider_name,
            model=model_name,
            source_ids=source_ids,
            source_hash=source_hash,
            input_sha256=input_sha256,
            authorization_id=exc.decision.authorization_id,
            error=exc.decision.reason,
        )
        raise

    prompt = EXTRACTION_PROMPT.format(document=source_content[:30_000])
    try:
        raw_content, provider_request_id, usage = _invoke_model(
            provider=provider_name,
            model=model_name,
            prompt=prompt,
            transport=authorization.transport,
            model_caller=model_caller,
            api_key=api_key,
        )
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, ClaimExtractionError)
            else ClaimExtractionError(f"model call failed: {exc}")
        )
        _write_extraction_receipt(
            vault,
            receipt_id,
            status="failed",
            provider=provider_name,
            model=model_name,
            source_ids=source_ids,
            source_hash=source_hash,
            input_sha256=input_sha256,
            authorization_id=authorization.authorization_id,
            error=str(error),
        )
        raise error from exc

    try:
        claims = _parse_claims(raw_content, source_content)
    except ClaimExtractionError as exc:
        _write_extraction_receipt(
            vault,
            receipt_id,
            status="failed",
            provider=provider_name,
            model=model_name,
            source_ids=source_ids,
            source_hash=source_hash,
            input_sha256=input_sha256,
            authorization_id=authorization.authorization_id,
            provider_request_id=provider_request_id,
            response_sha256=hashlib.sha256(raw_content.encode()).hexdigest(),
            usage=usage,
            error=str(exc),
        )
        raise

    existing_keys = _existing_claim_keys(vault)
    staged: list[str] = []
    skipped = 0
    now = datetime.now(UTC)
    for claim in claims:
        key = (claim.predicate.casefold(), claim.object_literal.casefold())
        if key in existing_keys:
            skipped += 1
            continue
        result = stage_claim(
            vault,
            subject_id=subject_id,
            predicate=claim.predicate,
            object_literal=claim.object_literal,
            source_ids=source_ids,
            evidence_anchor=claim.evidence_anchor,
            evidence_excerpt=claim.evidence_excerpt,
            claim_status="source-claimed",
            confidence=claim.confidence,
            observed_at=now,
        )
        staged.append(result["claim_id"])
        existing_keys.add(key)

    receipt_path = _write_extraction_receipt(
        vault,
        receipt_id,
        status="complete" if staged else "no_claims",
        provider=provider_name,
        model=model_name,
        source_ids=source_ids,
        source_hash=source_hash,
        input_sha256=input_sha256,
        authorization_id=authorization.authorization_id,
        provider_request_id=provider_request_id,
        response_sha256=hashlib.sha256(raw_content.encode()).hexdigest(),
        usage=usage,
        staged=len(staged),
        skipped=skipped,
        claim_ids=staged,
    )
    return {
        "status": "complete" if staged else "no_claims",
        "staged": len(staged),
        "skipped": skipped,
        "claim_ids": staged,
        "input_sha256": input_sha256,
        "receipt_path": receipt_path,
    }


def extract_claims_from_run(
    vault: Path | str,
    run_id: str,
    *,
    subject_id: str,
    provider: str | None = None,
    model: str | None = None,
    model_caller: Callable[..., object] | None = None,
    api_key: str | None = None,
) -> dict[str, object]:
    """Extract claims from all preserved sources in a research run.

    Returns combined stats across all sources.
    """
    vault = Path(vault).absolute()
    try:
        run_base = safe_relative_path(vault, Path(".constellation/research-runs") / run_id)
    except ValueError as exc:
        raise ClaimExtractionError(f"invalid research run ID: {run_id}") from exc
    receipt_path = run_base / "receipt.json"
    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise ClaimExtractionError(f"research run receipt not found: {run_id}")

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClaimExtractionError(f"research run receipt is invalid: {run_id}") from exc
    if not isinstance(receipt, dict):
        raise ClaimExtractionError(f"research run receipt is invalid: {run_id}")
    if receipt.get("status") != "completed" or receipt.get("promotion_allowed") is not True:
        raise ClaimExtractionError(f"research run is not promotion-allowed: {run_id}")
    sources = receipt.get("sources")
    if not isinstance(sources, list):
        raise ClaimExtractionError(f"research run sources are invalid: {run_id}")

    prepared: list[tuple[Path, list[str]]] = []
    for source in sources:
        source_hash = source.get("source_hash") if isinstance(source, dict) else None
        if not isinstance(source_hash, str) or len(source_hash) != 64:
            raise ClaimExtractionError(f"research run source hash is invalid: {run_id}")
        source_file = run_base / f"{source_hash}.md"
        if not source_file.is_file() or source_file.is_symlink():
            raise ClaimExtractionError(f"preserved source is missing: {source_hash}")
        actual_hash = hashlib.sha256(source_file.read_bytes()).hexdigest()
        if actual_hash != source_hash:
            raise ClaimExtractionError(f"preserved source hash mismatch: {source_hash}")
        prepared.append((source_file, _source_ids_for_hash(vault, source_hash)))

    total_staged = 0
    total_skipped = 0
    all_claim_ids: list[str] = []
    for source_file, source_ids in prepared:
        result = extract_claims_from_source(
            vault,
            source_file,
            subject_id=subject_id,
            source_ids=source_ids,
            provider=provider,
            model=model,
            model_caller=model_caller,
            api_key=api_key,
        )
        staged = result.get("staged")
        skipped = result.get("skipped")
        claim_ids = result.get("claim_ids")
        if not isinstance(staged, int) or not isinstance(skipped, int) or not isinstance(claim_ids, list):
            raise ClaimExtractionError("claim extraction returned invalid statistics")
        total_staged += staged
        total_skipped += skipped
        all_claim_ids.extend(str(claim_id) for claim_id in claim_ids)

    return {
        "status": "complete" if all_claim_ids else "no_claims",
        "staged": total_staged,
        "skipped": total_skipped,
        "claim_ids": all_claim_ids,
        "sources_processed": len(prepared),
    }
