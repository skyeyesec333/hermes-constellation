"""External intelligence feeders — GDELT, SEC EDGAR, Polymarket.

Each feeder queries one external API, preserves exact response bytes, creates
a source-item candidate through the ingest pipeline, and writes a terminal
receipt.  Claim extraction is a separate, explicit step — feeders never stage
claims inline.

Phase 16 rewrite: egress-gated, URL-safe, byte-bounded, receipt-tracked.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .egress import (
    EgressDecision,
    EgressDenied,
    EgressRequest,
    authorize_egress,
)
from .frontmatter import parse_frontmatter
from .ingest import ingest_file
from .models import EntityRecord, Sensitivity, SourceItem, generate_ulid
from .storage import atomic_write_bytes, atomic_write_text, safe_relative_path, sha256_bytes
from .url_safety import UnsafeUrlError, validate_http_url
from .vault import is_initialized

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MiB
_REQUEST_TIMEOUT = 30
_FEEDER_PURPOSE = "research"

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
EDGAR_API = "https://efts.sec.gov/LATEST/search-index"
POLYMARKET_API = "https://gamma-api.polymarket.com"

_GDELT_MODES = frozenset({"artlist", "timelinevol", "tonechart"})
_FEEDER_SOURCES = frozenset({"gdelt", "edgar", "polymarket"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FeederError(RuntimeError):
    """Raised when a feeder operation fails."""


# ---------------------------------------------------------------------------
# Request / result contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeederRequest:
    """One feeder collection request."""

    source: str  # "gdelt" | "edgar" | "polymarket"
    query: str
    subject_id: str
    provider: str  # egress provider name
    model: str     # egress model name
    max_results: int = 10

    def __post_init__(self) -> None:
        if self.source not in _FEEDER_SOURCES:
            raise FeederError(f"unsupported source: {self.source}")
        if not self.query.strip():
            raise FeederError("query is required")
        if not self.subject_id.strip():
            raise FeederError("subject_id is required")
        if not self.provider.strip() or not self.model.strip():
            raise FeederError("provider and model are required")


@dataclass(frozen=True, slots=True)
class FeederResult:
    status: str  # "ok" | "empty" | "denied" | "failed"
    source_ids: tuple[str, ...] = ()
    candidate_ids: tuple[str, ...] = ()
    receipt_path: str = ""
    items_found: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


def _write_feeder_receipt(
    vault: Path,
    receipt_id: str,
    *,
    status: str,
    source: str,
    provider: str,
    model: str,
    query: str,
    subject_id: str,
    authorization_id: str | None = None,
    source_ids: tuple[str, ...] | None = None,
    candidate_ids: tuple[str, ...] | None = None,
    items_found: int = 0,
    response_sha256: str | None = None,
    error: str | None = None,
) -> str:
    relative = Path(".constellation/feeder-receipts") / f"{receipt_id}.json"
    payload = {
        "schema_version": "0.1",
        "receipt_id": receipt_id,
        "status": status,
        "source": source,
        "provider": provider,
        "model": model,
        "query": query,
        "subject_id": subject_id,
        "authorization_id": authorization_id,
        "source_ids": list(source_ids or ()),
        "candidate_ids": list(candidate_ids or ()),
        "items_found": items_found,
        "response_sha256": response_sha256,
        "error": error,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(vault, relative, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return relative.as_posix()


# ---------------------------------------------------------------------------
# Entity / sensitivity helpers
# ---------------------------------------------------------------------------


def _require_canonical_subject(vault: Path, subject_id: str) -> EntityRecord:
    try:
        path = safe_relative_path(vault, Path("entities") / f"{subject_id}.md")
    except ValueError as exc:
        raise FeederError(f"canonical subject is invalid: {subject_id}") from exc
    if not path.is_file() or path.is_symlink():
        raise FeederError(f"canonical subject not found: {subject_id}")
    try:
        metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        subject = EntityRecord.model_validate(metadata, strict=False)
    except Exception as exc:
        raise FeederError(f"canonical subject is invalid: {subject_id}") from exc
    if subject.id != subject_id:
        raise FeederError(f"canonical subject does not match: {subject_id}")
    return subject


def _derive_sensitivity(subject: EntityRecord) -> Sensitivity:
    return subject.sensitivity


# ---------------------------------------------------------------------------
# Egress helper
# ---------------------------------------------------------------------------


def _authorize_feeder(
    vault: Path,
    *,
    provider: str,
    model: str,
    sensitivity: Sensitivity,
    query: str,
) -> EgressDecision:
    query_hash = hashlib.sha256(query.encode()).hexdigest()
    request = EgressRequest(
        provider=provider,
        model=model,
        purpose=_FEEDER_PURPOSE,
        sensitivity=sensitivity,
        request_input_sha256=query_hash,
    )
    decision = authorize_egress(vault, request)
    if not decision.allowed:
        raise EgressDenied(decision)
    return decision


# ---------------------------------------------------------------------------
# Safe HTTP fetch
# ---------------------------------------------------------------------------


def _safe_fetch(url: str, *, timeout: int = _REQUEST_TIMEOUT) -> bytes:
    """Validate URL, fetch with byte/timeout bounds, return response bytes."""
    try:
        safe_url = validate_http_url(url)
    except UnsafeUrlError as exc:
        raise FeederError(f"unsafe feeder URL: {exc}") from exc
    if not safe_url.startswith("https://"):
        raise FeederError("feeder URLs must use HTTPS")

    try:
        with urllib.request.urlopen(safe_url, timeout=timeout) as resp:
            data = resp.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.URLError as exc:
        raise FeederError(f"feeder API unreachable: {exc}") from exc

    if len(data) > _MAX_RESPONSE_BYTES:
        raise FeederError(f"feeder response exceeds {_MAX_RESPONSE_BYTES} bytes")
    return data


# ---------------------------------------------------------------------------
# Save response and call ingest
# ---------------------------------------------------------------------------


def _preserve_and_ingest(
    vault: Path,
    data: bytes,
    *,
    source_label: str,
    sensitivity: Sensitivity,
    source_url: str,
) -> dict[str, str]:
    """Write bytes to a temp file in the vault, then call ingest_file."""
    response_dir = vault / ".constellation" / "feeder-responses"
    response_dir.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(data)
    dest = response_dir / f"{digest}.json"
    # Only write if not already present (idempotent)
    if not dest.exists():
        atomic_write_bytes(vault, Path(".constellation/feeder-responses") / f"{digest}.json", data)
    return ingest_file(
        vault,
        dest,
        sensitivity=sensitivity,
        source_url=source_url,
        kind="generic",
    )


# ---------------------------------------------------------------------------
# GDELT
# ---------------------------------------------------------------------------


def _collect_gdelt(
    vault: Path,
    request: FeederRequest,
    *,
    subject: EntityRecord,
) -> FeederResult:
    mode = "artlist"
    params = {
        "query": request.query,
        "mode": mode,
        "maxrecords": str(request.max_results),
        "timespan": "7d",
        "format": "json",
    }
    url = f"{GDELT_API}?{urllib.parse.urlencode(params)}"

    sensitivity = _derive_sensitivity(subject)
    try:
        decision = _authorize_feeder(
            vault,
            provider=request.provider,
            model=request.model,
            sensitivity=sensitivity,
            query=request.query,
        )
    except EgressDenied:
        # Record denial without making a network call
        receipt_id = generate_ulid()
        receipt_path = _write_feeder_receipt(
            vault,
            receipt_id,
            status="denied",
            source=request.source,
            provider=request.provider,
            model=request.model,
            query=request.query,
            subject_id=request.subject_id,
            error="egress denied",
        )
        return FeederResult(status="denied", receipt_path=receipt_path)

    receipt_id = generate_ulid()
    try:
        data = _safe_fetch(url)
    except FeederError as exc:
        _write_feeder_receipt(
            vault,
            receipt_id,
            status="failed",
            source=request.source,
            provider=request.provider,
            model=request.model,
            query=request.query,
            subject_id=request.subject_id,
            authorization_id=decision.authorization_id,
            error=str(exc),
        )
        raise

    response_sha256 = sha256_bytes(data)
    body_text = data.decode("utf-8", errors="replace")
    if not body_text.strip():
        receipt_path = _write_feeder_receipt(
            vault,
            receipt_id,
            status="empty",
            source=request.source,
            provider=request.provider,
            model=request.model,
            query=request.query,
            subject_id=request.subject_id,
            authorization_id=decision.authorization_id,
            response_sha256=response_sha256,
        )
        return FeederResult(status="empty", receipt_path=receipt_path, items_found=0)

    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError as exc:
        error = f"GDELT returned invalid JSON: {exc}"
        _write_feeder_receipt(
            vault, receipt_id, status="failed", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            authorization_id=decision.authorization_id,
            response_sha256=response_sha256, error=error,
        )
        raise FeederError(error) from exc

    articles = payload.get("articles", []) if isinstance(payload, dict) else []
    if not articles:
        receipt_path = _write_feeder_receipt(
            vault, receipt_id, status="empty", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            authorization_id=decision.authorization_id,
            response_sha256=response_sha256, items_found=0,
        )
        return FeederResult(status="empty", receipt_path=receipt_path)

    # Preserve exact response bytes through ingest pipeline
    ingest_result = _preserve_and_ingest(
        vault, data,
        source_label="gdelt",
        sensitivity=sensitivity,
        source_url=url,
    )

    source_id = ingest_result.get("source_id", "")
    candidate_id = ingest_result.get("candidate_id", "")
    receipt_path = _write_feeder_receipt(
        vault, receipt_id, status="ok", source=request.source,
        provider=request.provider, model=request.model,
        query=request.query, subject_id=request.subject_id,
        authorization_id=decision.authorization_id,
        source_ids=(source_id,),
        candidate_ids=(candidate_id,),
        items_found=len(articles),
        response_sha256=response_sha256,
    )
    return FeederResult(
        status="ok",
        source_ids=(source_id,),
        candidate_ids=(candidate_id,),
        receipt_path=receipt_path,
        items_found=len(articles),
    )


# ---------------------------------------------------------------------------
# EDGAR
# ---------------------------------------------------------------------------


def _collect_edgar(
    vault: Path,
    request: FeederRequest,
    *,
    subject: EntityRecord,
) -> FeederResult:
    form_types = ["10-K", "8-K"]
    query_parts = [f'companyName:"{request.query}"']
    for ft in form_types:
        query_parts.append(f'formType:"{ft}"')
    q = " AND ".join(query_parts)
    params = {
        "q": q,
        "sort": "filedAt",
        "order": "desc",
        "pageSize": str(request.max_results),
    }
    url = f"{EDGAR_API}?{urllib.parse.urlencode(params)}"

    sensitivity = _derive_sensitivity(subject)
    try:
        decision = _authorize_feeder(
            vault, provider=request.provider, model=request.model,
            sensitivity=sensitivity, query=request.query,
        )
    except EgressDenied:
        receipt_id = generate_ulid()
        receipt_path = _write_feeder_receipt(
            vault, receipt_id, status="denied", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            error="egress denied",
        )
        return FeederResult(status="denied", receipt_path=receipt_path)

    receipt_id = generate_ulid()
    try:
        safe_url = validate_http_url(url)
        if not safe_url.startswith("https://"):
            raise FeederError("EDGAR URL must use HTTPS")
        req = urllib.request.Request(
            safe_url,
            headers={"User-Agent": "Constellation/0.2 (" + "contact" + "@" + "example.test" + ")"},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = resp.read(_MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, UnsafeUrlError) as exc:
        error = f"EDGAR API unreachable: {exc}"
        _write_feeder_receipt(
            vault, receipt_id, status="failed", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            authorization_id=decision.authorization_id, error=error,
        )
        raise FeederError(error) from exc

    if len(data) > _MAX_RESPONSE_BYTES:
        error = f"EDGAR response exceeds {_MAX_RESPONSE_BYTES} bytes"
        _write_feeder_receipt(
            vault, receipt_id, status="failed", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            authorization_id=decision.authorization_id, error=error,
        )
        raise FeederError(error)

    response_sha256 = sha256_bytes(data)
    try:
        body = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        error = f"EDGAR returned invalid JSON: {exc}"
        _write_feeder_receipt(
            vault, receipt_id, status="failed", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            authorization_id=decision.authorization_id,
            response_sha256=response_sha256, error=error,
        )
        raise FeederError(error) from exc

    hits = body.get("hits", {}).get("hits", []) if isinstance(body, dict) else []
    if not hits:
        receipt_path = _write_feeder_receipt(
            vault, receipt_id, status="empty", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            authorization_id=decision.authorization_id,
            response_sha256=response_sha256, items_found=0,
        )
        return FeederResult(status="empty", receipt_path=receipt_path)

    ingest_result = _preserve_and_ingest(
        vault, data, source_label="edgar",
        sensitivity=sensitivity, source_url=url,
    )
    source_id = ingest_result.get("source_id", "")
    candidate_id = ingest_result.get("candidate_id", "")
    receipt_path = _write_feeder_receipt(
        vault, receipt_id, status="ok", source=request.source,
        provider=request.provider, model=request.model,
        query=request.query, subject_id=request.subject_id,
        authorization_id=decision.authorization_id,
        source_ids=(source_id,),
        candidate_ids=(candidate_id,),
        items_found=len(hits),
        response_sha256=response_sha256,
    )
    return FeederResult(
        status="ok", source_ids=(source_id,), candidate_ids=(candidate_id,),
        receipt_path=receipt_path, items_found=len(hits),
    )


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------


def _collect_polymarket(
    vault: Path,
    request: FeederRequest,
    *,
    subject: EntityRecord,
) -> FeederResult:
    params = {"query": request.query, "limit": str(request.max_results)}
    url = f"{POLYMARKET_API}/markets?{urllib.parse.urlencode(params)}"

    sensitivity = _derive_sensitivity(subject)
    try:
        decision = _authorize_feeder(
            vault, provider=request.provider, model=request.model,
            sensitivity=sensitivity, query=request.query,
        )
    except EgressDenied:
        receipt_id = generate_ulid()
        receipt_path = _write_feeder_receipt(
            vault, receipt_id, status="denied", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            error="egress denied",
        )
        return FeederResult(status="denied", receipt_path=receipt_path)

    receipt_id = generate_ulid()
    try:
        data = _safe_fetch(url)
    except FeederError as exc:
        _write_feeder_receipt(
            vault, receipt_id, status="failed", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            authorization_id=decision.authorization_id, error=str(exc),
        )
        raise

    response_sha256 = sha256_bytes(data)
    try:
        body = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        error = f"Polymarket returned invalid JSON: {exc}"
        _write_feeder_receipt(
            vault, receipt_id, status="failed", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            authorization_id=decision.authorization_id,
            response_sha256=response_sha256, error=error,
        )
        raise FeederError(error) from exc

    if not isinstance(body, list) or not body:
        receipt_path = _write_feeder_receipt(
            vault, receipt_id, status="empty", source=request.source,
            provider=request.provider, model=request.model,
            query=request.query, subject_id=request.subject_id,
            authorization_id=decision.authorization_id,
            response_sha256=response_sha256, items_found=0,
        )
        return FeederResult(status="empty", receipt_path=receipt_path)

    ingest_result = _preserve_and_ingest(
        vault, data, source_label="polymarket",
        sensitivity=sensitivity, source_url=url,
    )
    source_id = ingest_result.get("source_id", "")
    candidate_id = ingest_result.get("candidate_id", "")
    receipt_path = _write_feeder_receipt(
        vault, receipt_id, status="ok", source=request.source,
        provider=request.provider, model=request.model,
        query=request.query, subject_id=request.subject_id,
        authorization_id=decision.authorization_id,
        source_ids=(source_id,),
        candidate_ids=(candidate_id,),
        items_found=len(body),
        response_sha256=response_sha256,
    )
    return FeederResult(
        status="ok", source_ids=(source_id,), candidate_ids=(candidate_id,),
        receipt_path=receipt_path, items_found=len(body),
    )


# ---------------------------------------------------------------------------
# Unified entry points
# ---------------------------------------------------------------------------

_COLLECTORS: dict[str, Callable[..., FeederResult]] = {
    "gdelt": _collect_gdelt,
    "edgar": _collect_edgar,
    "polymarket": _collect_polymarket,
}


# ---------------------------------------------------------------------------
# Circuit breaker — consecutive-failure protection per feeder lane
# ---------------------------------------------------------------------------

_CIRCUIT_THRESHOLD = 3


def _circuit_state_path(vault: Path) -> Path:
    return Path(".constellation/feeder-health.json")


def _load_circuit_state(vault: Path) -> dict:
    path = vault / _circuit_state_path(vault)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_circuit_state(vault: Path, state: dict) -> None:
    atomic_write_text(
        vault,
        _circuit_state_path(vault),
        json.dumps(state, indent=2, sort_keys=True) + "\n",
    )


def _record_feeder_outcome(vault: Path, source: str, status: str) -> None:
    """Track consecutive failures per lane. `denied` reflects configuration,
    not source health, and never counts toward the circuit."""
    if status == "denied":
        return
    state = _load_circuit_state(vault)
    lane = state.get(source, {})
    if status == "failed":
        lane["consecutive_failures"] = int(lane.get("consecutive_failures", 0)) + 1
        lane["last_failure_at"] = datetime.now(UTC).isoformat()
    else:
        lane["consecutive_failures"] = 0
    lane["last_status"] = status
    state[source] = lane
    _save_circuit_state(vault, state)


def reset_feeder_circuit(vault: Path | str, source: str) -> dict:
    """Manually reopen a feeder lane after its circuit has opened."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise FeederError("vault is not initialized")
    state = _load_circuit_state(vault)
    lane = state.get(source, {})
    lane["consecutive_failures"] = 0
    lane.pop("last_failure_at", None)
    lane["last_status"] = "reset"
    state[source] = lane
    _save_circuit_state(vault, state)
    return {"status": "reset", "source": source}


def feeder_circuit_status(vault: Path | str) -> dict:
    """Report per-lane circuit state for operator visibility."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise FeederError("vault is not initialized")
    state = _load_circuit_state(vault)
    lanes = {
        source: {
            "consecutive_failures": int(lane.get("consecutive_failures", 0)),
            "circuit_open": int(lane.get("consecutive_failures", 0)) >= _CIRCUIT_THRESHOLD,
            "last_status": lane.get("last_status"),
        }
        for source, lane in sorted(state.items())
    }
    return {"threshold": _CIRCUIT_THRESHOLD, "lanes": lanes}


def collect_from_feeder(vault: Path | str, request: FeederRequest) -> FeederResult:
    """Query an external API and preserve exact bytes as a source candidate.

    Does NOT stage claims — that is a separate, explicit step.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise FeederError("vault is not initialized")

    subject = _require_canonical_subject(vault, request.subject_id)
    collector = _COLLECTORS.get(request.source)
    if collector is None:
        raise FeederError(f"unsupported source: {request.source}")

    lane = _load_circuit_state(vault).get(request.source, {})
    failures = int(lane.get("consecutive_failures", 0))
    if failures >= _CIRCUIT_THRESHOLD:
        error = (
            f"feeder circuit open for {request.source}: "
            f"{failures} consecutive failures; reset explicitly to retry"
        )
        receipt_path = _write_feeder_receipt(
            vault,
            generate_ulid(),
            status="circuit_open",
            source=request.source,
            provider=request.provider,
            model=request.model,
            query=request.query,
            subject_id=request.subject_id,
            error=error,
        )
        return FeederResult(status="circuit_open", receipt_path=receipt_path, error=error)

    result = collector(vault, request, subject=subject)
    _record_feeder_outcome(vault, request.source, result.status)
    return result


def extract_from_feeder_source(
    vault: Path | str,
    source_id: str,
    *,
    subject_id: str,
    provider: str,
    model: str,
    model_caller: Callable[..., object] | None = None,
    api_key: str | None = None,
) -> dict[str, object]:
    """Extract claims from a promoted feeder source-item.

    Only call after the source candidate has been reviewed and promoted.
    This delegates to claim_extractor for the actual extraction.
    """
    from .claim_extractor import extract_claims_from_source

    vault = Path(vault).absolute()
    # Verify the source item exists and is canonical
    source_path = safe_relative_path(vault, Path("source-items") / f"{source_id}.md")
    if not source_path.is_file() or source_path.is_symlink():
        raise FeederError(f"canonical source item not found: {source_id}")

    metadata, _ = parse_frontmatter(source_path.read_text(encoding="utf-8"))
    source_item = SourceItem.model_validate(metadata, strict=False)
    if source_item.id != source_id:
        raise FeederError(f"source item ID mismatch: {source_id}")

    # Find the preserved file for this source
    manifest_path = vault / ".constellation/manifests" / f"{source_item.source_hash}.json"
    if not manifest_path.is_file():
        raise FeederError(f"source manifest not found for: {source_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    preserved_rel = manifest.get("text_path") or manifest.get("preserved_path")
    if not preserved_rel:
        raise FeederError(f"preserved file path not found in manifest: {source_id}")

    source_file = safe_relative_path(vault, Path(preserved_rel))
    if not source_file.is_file():
        raise FeederError(f"preserved source file not found: {preserved_rel}")

    return extract_claims_from_source(
        vault,
        source_file,
        subject_id=subject_id,
        source_ids=[source_id],
        provider=provider,
        model=model,
        model_caller=model_caller,
        api_key=api_key,
    )
