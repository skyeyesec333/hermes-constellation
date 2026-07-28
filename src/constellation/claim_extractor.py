"""Auto claim extraction from preserved research sources.

Phase 13: reads preserved source markdown, calls an LLM to extract factual
assertions, deduplicates against existing claims, and stages candidates.
Never auto-promotes — all claims are review-required.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .claim import stage_claim
from .egress import EgressDenied, EgressRequest, require_egress
from .frontmatter import parse_frontmatter
from .models import Claim, ClaimStatus, Sensitivity, SourceItem, generate_ulid
from .storage import atomic_write_text, safe_relative_path
from .identity import SubjectResolutionError, resolve_subject
from .url_safety import UnsafeUrlError, validate_http_url
from .vault import is_initialized


class ClaimExtractionError(RuntimeError):
    """Raised when claim extraction fails."""


_MAX_MODEL_RESPONSE_BYTES = 1_000_000
_MAX_MODEL_TOKENS = 4_000
_MAX_SOURCE_CHUNK_CHARS = 6_000
_MAX_USAGE_COUNTER = 1_000_000_000_000
_CONFIDENCE = {"direct_quote": 0.95, "paraphrase": 0.85, "inference": 0.70}
_USAGE_FIELDS = {
    "tokens",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
}
_LINE_BREAK_RE = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_TRAILING_LINE_BREAK_RE = re.compile(r"(?:\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029])\Z")


def _line_break_count(text: str) -> int:
    return len(_LINE_BREAK_RE.findall(text))


def _ends_with_line_break(text: str) -> bool:
    return _TRAILING_LINE_BREAK_RE.search(text) is not None


def _without_trailing_line_break(text: str) -> str:
    return _TRAILING_LINE_BREAK_RE.sub("", text)


def _decode_source_bytes(source_bytes: bytes, *, label: str) -> str:
    try:
        source_content = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClaimExtractionError(f"{label} is not valid UTF-8") from exc
    if not source_content.strip():
        raise ClaimExtractionError(f"{label} is empty")
    return source_content


@dataclass(frozen=True, slots=True)
class _SourceChunk:
    index: int
    start_line: int
    end_line: int
    text: str
    sha256: str


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


def _chunk_source(source_content: str) -> list[_SourceChunk]:
    """Split exact source text into deterministic paragraph/line chunks."""
    paragraph_units: list[str] = []
    paragraph_lines: list[str] = []
    for line in source_content.splitlines(keepends=True):
        paragraph_lines.append(line)
        if not _without_trailing_line_break(line):
            paragraph_units.append("".join(paragraph_lines))
            paragraph_lines = []
    if paragraph_lines:
        paragraph_units.append("".join(paragraph_lines))

    chunk_texts: list[str] = []
    pending = ""

    def flush_pending() -> None:
        nonlocal pending
        if pending:
            chunk_texts.append(pending)
            pending = ""

    for paragraph in paragraph_units:
        if len(paragraph) <= _MAX_SOURCE_CHUNK_CHARS:
            if pending and len(pending) + len(paragraph) > _MAX_SOURCE_CHUNK_CHARS:
                flush_pending()
            pending += paragraph
            continue

        flush_pending()
        for line in paragraph.splitlines(keepends=True):
            if len(line) > _MAX_SOURCE_CHUNK_CHARS:
                flush_pending()
                chunk_texts.append(line)
            else:
                if pending and len(pending) + len(line) > _MAX_SOURCE_CHUNK_CHARS:
                    flush_pending()
                pending += line
    flush_pending()

    chunks: list[_SourceChunk] = []
    start_line = 1
    for index, text in enumerate(chunk_texts):
        line_break_count = _line_break_count(text)
        end_line = start_line + line_break_count - int(_ends_with_line_break(text))
        chunks.append(
            _SourceChunk(
                index=index,
                start_line=start_line,
                end_line=end_line,
                text=text,
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
        start_line += line_break_count
    return chunks


def _extraction_input_sha256(source_content: str, source_hash: str) -> str:
    """Stable hash of the extraction input for receipt traceability."""
    packet = json.dumps(
        {
            "source_hash": source_hash,
            "content_sha256": hashlib.sha256(source_content.encode()).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(packet.encode()).hexdigest()


def _run_preflight_input_sha256(source_hash: str) -> str:
    packet = json.dumps(
        {"source_hash": source_hash, "stage": "run_preflight"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(packet.encode("utf-8")).hexdigest()


def _chunk_input_sha256(chunk: _SourceChunk, source_hash: str, prompt: str) -> str:
    packet = json.dumps(
        {
            "source_hash": source_hash,
            "chunk_sha256": chunk.sha256,
            "chunk_index": chunk.index,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(packet.encode("utf-8")).hexdigest()


def _bounded_usage(usage: object) -> dict[str, int | float] | None:
    if not isinstance(usage, dict):
        return None
    bounded: dict[str, int | float] = {}
    for key in sorted(_USAGE_FIELDS):
        value = usage.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and 0 <= value <= _MAX_USAGE_COUNTER:
            bounded[key] = value
        elif (
            isinstance(value, float)
            and math.isfinite(value)
            and 0 <= value <= _MAX_USAGE_COUNTER
        ):
            bounded[key] = value
    return bounded or None


def _optional_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    provider_request_id_sha256: str | None = None,
    response_sha256: str | None = None,
    usage: object = None,
    staged: int = 0,
    skipped: int = 0,
    claim_ids: list[str] | None = None,
    chunks: list[dict[str, object]] | None = None,
    error: str | None = None,
) -> str:
    relative = Path(".constellation/claim-extractions") / f"{receipt_id}.json"
    failed_chunk = next(
        (
            {
                "chunk_index": chunk.get("chunk_index"),
                "start_line": chunk.get("start_line"),
                "end_line": chunk.get("end_line"),
            }
            for chunk in chunks or []
            if chunk.get("status") in {"failed", "denied"}
        ),
        None,
    )
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
        "provider_request_id_sha256": provider_request_id_sha256,
        "response_sha256": response_sha256,
        "usage": usage,
        "staged": staged,
        "skipped": skipped,
        "claim_ids": claim_ids or [],
        "chunk_count": len(chunks or []),
        "chunks": chunks or [],
        "failed_chunk": failed_chunk,
        "authorization_ids": [
            value
            for chunk in chunks or []
            if isinstance((value := chunk.get("authorization_id")), str)
        ],
        "error": error,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(vault, relative, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return relative.as_posix()


def _exact_line_anchor(source_content: str, excerpt: str, *, first_line: int = 1) -> str:
    start = source_content.find(excerpt)
    if start < 0:
        raise ClaimExtractionError("claim evidence excerpt was not found exactly in the source")
    start_line = first_line + _line_break_count(source_content[:start])
    end_line = start_line + _line_break_count(excerpt)
    return f"L{start_line:06d}-L{end_line:06d}"


def _load_claim_payload(raw_content: str) -> object:
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError as direct_error:
        decoder = json.JSONDecoder()
        arrays: list[object] = []
        cursor = 0
        while (start := raw_content.find("[", cursor)) >= 0:
            try:
                candidate, end = decoder.raw_decode(raw_content, start)
            except json.JSONDecodeError:
                cursor = start + 1
                continue
            if isinstance(candidate, list):
                arrays.append(candidate)
                cursor = end
            else:
                cursor = start + 1
        if len(arrays) == 1:
            return arrays[0]
        raise ClaimExtractionError("failed to parse model claims JSON") from direct_error


def _parse_claims(
    raw_content: str, source_content: str, *, first_line: int = 1
) -> list[_ExtractedClaim]:
    payload = _load_claim_payload(raw_content)
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
                evidence_anchor=_exact_line_anchor(
                    source_content, evidence, first_line=first_line
                ),
                confidence=_CONFIDENCE[confidence_name],
            )
        )
    return claims


def _validate_extracted_claim(
    claim: _ExtractedClaim,
    *,
    subject_id: str,
    source_ids: list[str],
    observed_at: datetime,
) -> None:
    try:
        Claim(
            type="claim",
            title=f"claim-{subject_id[:8]}-{claim.predicate}",
            status="review-required",
            sensitivity=Sensitivity.INTERNAL,
            subject_id=subject_id,
            predicate=claim.predicate,
            object_literal=claim.object_literal,
            source_ids=source_ids,
            evidence_anchor=claim.evidence_anchor,
            evidence_excerpt=claim.evidence_excerpt,
            claim_status=ClaimStatus.SOURCE_CLAIMED,
            confidence=claim.confidence,
            observed_at=observed_at,
            created_at=observed_at,
            updated_at=observed_at,
        )
    except ValueError as exc:
        raise ClaimExtractionError("model claim failed schema validation") from exc


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


def _model_max_tokens() -> int:
    setting = os.environ.get("CONSTELLATION_MODEL_MAX_TOKENS", str(_MAX_MODEL_TOKENS))
    try:
        max_tokens = int(setting)
    except ValueError as exc:
        raise ClaimExtractionError(
            "CONSTELLATION_MODEL_MAX_TOKENS must be an integer from 1 to 16000"
        ) from exc
    if not 1 <= max_tokens <= 16_000:
        raise ClaimExtractionError(
            "CONSTELLATION_MODEL_MAX_TOKENS must be an integer from 1 to 16000"
        )
    return max_tokens


def _invoke_model(
    *,
    provider: str,
    model: str,
    prompt: str,
    max_tokens: int,
    transport: str | None,
    model_caller: Callable[..., object] | None,
    api_key: str | None,
) -> tuple[str, str | None, object]:
    if model_caller is not None:
        response = model_caller(
            provider=provider,
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
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
        "max_tokens": max_tokens,
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
    timeout_setting = os.environ.get("CONSTELLATION_MODEL_TIMEOUT_SECONDS", "60")
    try:
        timeout_seconds = int(timeout_setting)
    except ValueError as exc:
        raise ClaimExtractionError(
            "CONSTELLATION_MODEL_TIMEOUT_SECONDS must be an integer from 1 to 300"
        ) from exc
    if not 1 <= timeout_seconds <= 300:
        raise ClaimExtractionError(
            "CONSTELLATION_MODEL_TIMEOUT_SECONDS must be an integer from 1 to 300"
        )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
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


def _existing_claim_keys(vault: Path, subject_id: str) -> set[tuple[str, str]]:
    """Build dedup keys for one subject from claims and candidates."""
    keys: set[tuple[str, str]] = set()

    def add_key(payload: object) -> None:
        if not isinstance(payload, dict) or payload.get("subject_id") != subject_id:
            return
        predicate = str(payload.get("predicate", "")).strip().casefold()
        object_literal = str(payload.get("object_literal", "")).strip().casefold()
        if predicate and object_literal:
            keys.add((predicate, object_literal))

    claims_dir = vault / "claims"
    if claims_dir.is_dir():
        for path in claims_dir.glob("*.md"):
            try:
                frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
                add_key(frontmatter)
            except Exception:
                continue

    candidates_dir = vault / ".constellation/candidates"
    if candidates_dir.is_dir():
        for path in candidates_dir.glob("claim-*.json"):
            try:
                add_key(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
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

    source_bytes = src.read_bytes()
    source_content = _decode_source_bytes(source_bytes, label="source file")

    source_hash = hashlib.sha256(source_bytes).hexdigest()
    input_sha256 = _extraction_input_sha256(source_content, source_hash)
    provider_name = (provider or "").strip()
    model_name = (model or "").strip()
    if not provider_name or not model_name:
        raise ClaimExtractionError("provider and model are required")

    receipt_id = generate_ulid()
    try:
        max_tokens = _model_max_tokens()
        _require_canonical_subject(vault, subject_id)
        source_items = _require_matching_source_items(vault, source_ids, source_hash)
        sensitivity_order = tuple(Sensitivity)
        canonical_sensitivity = max(
            (item.sensitivity for item in source_items),
            key=sensitivity_order.index,
        )
    except ClaimExtractionError:
        _write_extraction_receipt(
            vault,
            receipt_id,
            status="failed",
            provider=provider_name,
            model=model_name,
            source_ids=source_ids,
            source_hash=source_hash,
            input_sha256=input_sha256,
            error="preflight_failed",
        )
        raise

    chunks = _chunk_source(source_content)
    claims: list[_ExtractedClaim] = []
    chunk_metadata: list[dict[str, object]] = []
    last_authorization_id: str | None = None
    last_provider_request_id_sha256: str | None = None
    last_response_sha256: str | None = None
    last_usage: object = None
    observed_at = datetime.now(UTC)

    for chunk in chunks:
        prompt = EXTRACTION_PROMPT.format(document=chunk.text)
        chunk_input_sha256 = _chunk_input_sha256(chunk, source_hash, prompt)
        metadata: dict[str, object] = {
            "chunk_index": chunk.index,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "chunk_sha256": chunk.sha256,
            "request_input_sha256": chunk_input_sha256,
        }
        try:
            authorization = require_egress(
                vault,
                EgressRequest(
                    provider=provider_name,
                    model=model_name,
                    purpose="stage1",
                    sensitivity=canonical_sensitivity,
                    source_hashes=(source_hash,),
                    request_input_sha256=chunk_input_sha256,
                ),
            )
        except EgressDenied as exc:
            metadata.update(
                status="denied",
                authorization_id=exc.decision.authorization_id,
                request_sha256=exc.decision.request_sha256,
            )
            chunk_metadata.append(metadata)
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
                chunks=chunk_metadata,
                error=exc.decision.reason,
            )
            raise

        last_authorization_id = authorization.authorization_id
        metadata["authorization_id"] = authorization.authorization_id
        metadata["request_sha256"] = authorization.request_sha256
        try:
            raw_content, provider_request_id, usage = _invoke_model(
                provider=provider_name,
                model=model_name,
                prompt=prompt,
                max_tokens=max_tokens,
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
            metadata["status"] = "failed"
            chunk_metadata.append(metadata)
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
                chunks=chunk_metadata,
                error="model_call_failed",
            )
            raise error from exc

        response_sha256 = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
        provider_request_id_sha256 = _optional_sha256(provider_request_id)
        bounded_usage = _bounded_usage(usage)
        metadata.update(
            provider_request_id_sha256=provider_request_id_sha256,
            response_sha256=response_sha256,
            usage=bounded_usage,
        )
        last_provider_request_id_sha256 = provider_request_id_sha256
        last_response_sha256 = response_sha256
        last_usage = bounded_usage
        try:
            chunk_claims = _parse_claims(
                raw_content, chunk.text, first_line=chunk.start_line
            )
            for claim in chunk_claims:
                _validate_extracted_claim(
                    claim,
                    subject_id=subject_id,
                    source_ids=source_ids,
                    observed_at=observed_at,
                )
        except ClaimExtractionError:
            metadata["status"] = "failed"
            chunk_metadata.append(metadata)
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
                provider_request_id_sha256=provider_request_id_sha256,
                response_sha256=response_sha256,
                usage=bounded_usage,
                chunks=chunk_metadata,
                error="model_response_invalid",
            )
            raise
        metadata["status"] = "complete"
        chunk_metadata.append(metadata)
        claims.extend(chunk_claims)

    existing_keys = _existing_claim_keys(vault, subject_id)
    staged: list[str] = []
    skipped = 0
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
            observed_at=observed_at,
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
        authorization_id=last_authorization_id,
        provider_request_id_sha256=last_provider_request_id_sha256,
        response_sha256=last_response_sha256,
        usage=last_usage,
        staged=len(staged),
        skipped=skipped,
        claim_ids=staged,
        chunks=chunk_metadata,
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
    source_hashes: list[str] = []
    for source in sources:
        source_hash = source.get("source_hash") if isinstance(source, dict) else None
        if not isinstance(source_hash, str) or len(source_hash) != 64:
            raise ClaimExtractionError(f"research run source hash is invalid: {run_id}")
        source_hashes.append(source_hash)

    provider_name = (provider or "").strip()
    model_name = (model or "").strip()
    try:
        if not provider_name or not model_name:
            raise ClaimExtractionError("provider and model are required for claim extraction")
        _model_max_tokens()
        _require_canonical_subject(vault, subject_id)
    except ClaimExtractionError:
        for source_hash in source_hashes:
            try:
                source_ids = _source_ids_for_hash(vault, source_hash)
            except ClaimExtractionError:
                source_ids = []
            _write_extraction_receipt(
                vault,
                generate_ulid(),
                status="failed",
                provider=provider_name,
                model=model_name,
                source_ids=source_ids,
                source_hash=source_hash,
                input_sha256=_run_preflight_input_sha256(source_hash),
                error="preflight_failed",
            )
        raise

    preflight_rows: list[
        tuple[Path, str, list[str], str, ClaimExtractionError | None]
    ] = []
    for source_hash in source_hashes:
        source_file = run_base / f"{source_hash}.md"
        source_ids: list[str] = []
        input_sha256 = _run_preflight_input_sha256(source_hash)
        failure: ClaimExtractionError | None = None
        try:
            if not source_file.is_file() or source_file.is_symlink():
                raise ClaimExtractionError(f"preserved source is missing: {source_hash}")
            try:
                source_bytes = source_file.read_bytes()
            except OSError as exc:
                raise ClaimExtractionError(
                    f"preserved source could not be read: {source_hash}"
                ) from exc
            actual_hash = hashlib.sha256(source_bytes).hexdigest()
            if actual_hash != source_hash:
                raise ClaimExtractionError(f"preserved source hash mismatch: {source_hash}")
            source_content = _decode_source_bytes(
                source_bytes, label=f"preserved source {source_hash}"
            )
            input_sha256 = _extraction_input_sha256(source_content, source_hash)
            source_ids = _source_ids_for_hash(vault, source_hash)
            _require_matching_source_items(vault, source_ids, source_hash)
        except ClaimExtractionError as exc:
            failure = exc
        preflight_rows.append(
            (source_file, source_hash, source_ids, input_sha256, failure)
        )

    first_failure = next(
        (row[4] for row in preflight_rows if row[4] is not None),
        None,
    )
    if first_failure is not None:
        for _, source_hash, source_ids, input_sha256, failure in preflight_rows:
            _write_extraction_receipt(
                vault,
                generate_ulid(),
                status="failed",
                provider=provider_name,
                model=model_name,
                source_ids=source_ids,
                source_hash=source_hash,
                input_sha256=input_sha256,
                error="preflight_failed" if failure is not None else "run_preflight_aborted",
            )
        raise first_failure

    prepared = [(row[0], row[2]) for row in preflight_rows]

    total_staged = 0
    total_skipped = 0
    all_claim_ids: list[str] = []
    for source_file, source_ids in prepared:
        result = extract_claims_from_source(
            vault,
            source_file,
            subject_id=subject_id,
            source_ids=source_ids,
            provider=provider_name,
            model=model_name,
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
