"""Auto claim extraction from preserved research sources.

Phase 13: reads preserved source markdown, calls an LLM to extract factual
assertions, deduplicates against existing claims, and stages candidates.
Never auto-promotes — all claims are review-required.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, UTC
from pathlib import Path

from .claim import stage_claim
from .frontmatter import parse_frontmatter
from .vault import is_initialized


class ClaimExtractionError(RuntimeError):
    """Raised when claim extraction fails."""


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
    api_key: str | None = None,
    model: str = "deepseek-chat",
) -> dict[str, object]:
    """Extract claims from a preserved source and stage them as candidates.

    Args:
        vault: Path to the Constellation vault
        source_path: Path to the preserved source markdown file
        subject_id: ULID of the entity the claims are about
        source_ids: ULIDs of source items these claims reference
        api_key: DeepSeek API key (defaults to DEEPSEEK_API_KEY env var)
        model: DeepSeek model to use

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

    # Get DeepSeek API key
    import os
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise ClaimExtractionError("DeepSeek API key not found. Set DEEPSEEK_API_KEY.")

    # Build prompt
    prompt = EXTRACTION_PROMPT.format(document=source_content[:30_000])
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    request = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ClaimExtractionError(f"DeepSeek API unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ClaimExtractionError(f"DeepSeek returned invalid JSON: {exc}") from exc

    # Parse response
    choices = body.get("choices", [])
    if not choices:
        raise ClaimExtractionError("DeepSeek returned no choices")

    raw_content = choices[0].get("message", {}).get("content", "")
    if not raw_content:
        raise ClaimExtractionError("DeepSeek returned empty content")

    # Parse claims from LLM response
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ClaimExtractionError(f"Failed to parse LLM claims JSON: {exc}") from exc

    if not isinstance(parsed, (list, dict)):
        raise ClaimExtractionError("LLM returned unexpected format, expected JSON array or object")

    # Handle both {"claims": [...]} and [...] formats
    claims_list = parsed if isinstance(parsed, list) else parsed.get("claims", [])
    if not isinstance(claims_list, list):
        raise ClaimExtractionError("LLM claims are not a list")

    if not claims_list:
        return {"status": "no_claims", "staged": 0, "skipped": 0, "claim_ids": []}

    # Dedup against existing claims
    existing_keys = _existing_claim_keys(vault)

    staged = []
    skipped = 0
    confidence_map = {"direct_quote": 0.95, "paraphrase": 0.85, "inference": 0.70}

    now = datetime.now(UTC)
    for claim_data in claims_list:
        if not isinstance(claim_data, dict):
            continue
        predicate = str(claim_data.get("predicate", "")).strip()
        object_literal = str(claim_data.get("object_literal", "")).strip()
        evidence = str(claim_data.get("evidence_excerpt", ""))[:300]
        confidence_str = str(claim_data.get("confidence", "paraphrase"))

        if not predicate or not object_literal:
            continue

        # Dedup
        key = (predicate.casefold(), object_literal.casefold())
        if key in existing_keys:
            skipped += 1
            continue

        confidence = confidence_map.get(confidence_str, 0.85)

        try:
            result = stage_claim(
                vault,
                subject_id=subject_id,
                predicate=predicate,
                object_literal=object_literal,
                source_ids=source_ids,
                evidence_excerpt=evidence,
                claim_status="source-claimed",
                confidence=confidence,
                observed_at=now,
            )
            staged.append(result["claim_id"])
            existing_keys.add(key)
        except Exception:
            skipped += 1

    return {
        "status": "complete" if staged else "no_claims",
        "staged": len(staged),
        "skipped": skipped,
        "claim_ids": staged,
        "input_sha256": input_sha256,
    }


def extract_claims_from_run(
    vault: Path | str,
    run_id: str,
    *,
    subject_id: str,
    api_key: str | None = None,
) -> dict[str, object]:
    """Extract claims from all preserved sources in a research run.

    Returns combined stats across all sources.
    """
    vault = Path(vault).absolute()
    run_dir = vault / ".constellation/research-runs" / run_id / "receipt.json"
    if not run_dir.is_file():
        raise ClaimExtractionError(f"research run receipt not found: {run_id}")

    receipt = json.loads(run_dir.read_text(encoding="utf-8"))
    sources = receipt.get("sources", [])
    run_base = vault / ".constellation/research-runs" / run_id

    total_staged = 0
    total_skipped = 0
    all_claim_ids: list[str] = []

    for source in sources:
        if not isinstance(source, dict):
            continue
        shash = source.get("source_hash", "")
        if not shash:
            continue

        source_file = run_base / f"{shash}.md"
        if not source_file.is_file():
            continue

        result = extract_claims_from_source(
            vault,
            source_file,
            subject_id=subject_id,
            source_ids=[],  # Will be filled from source-item lookups
            api_key=api_key,
        )
        total_staged += int(result.get("staged", 0))
        total_skipped += int(result.get("skipped", 0))
        all_claim_ids.extend(result.get("claim_ids", []))

    return {
        "status": "complete" if all_claim_ids else "no_claims",
        "staged": total_staged,
        "skipped": total_skipped,
        "claim_ids": all_claim_ids,
        "sources_processed": len(sources),
    }
