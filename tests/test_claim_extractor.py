import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from constellation import claim_extractor
from constellation.claim import stage_claim
from constellation.claim_extractor import (
    ClaimExtractionError,
    extract_claims_from_run,
    extract_claims_from_source,
)
from constellation.egress import EgressDenied
from constellation.frontmatter import render_frontmatter
from constellation.models import EntityKind, EntityRecord, Sensitivity, SourceItem, generate_ulid
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
PROVIDER = "test-provider"
MODEL = "fictional-model-v1"
EVIDENCE = "Acme Example appointed Alex Example as chief tester."
SOURCE_TEXT = f"# Fictional source\n\n{EVIDENCE}\n"


def _claim_response(
    *,
    evidence: str = EVIDENCE,
    request_id: str = "request-1",
    predicate: str = "appointed_as",
    object_literal: str = "chief tester",
) -> dict[str, object]:
    content = json.dumps(
        [
            {
                "predicate": predicate,
                "object_literal": object_literal,
                "evidence_excerpt": evidence,
                "confidence": "direct_quote",
            }
        ]
    )
    return {"content": content, "provider_request_id": request_id, "usage": {"input_tokens": 1}}


def _declare_model(vault: Path, *, max_sensitivity: str = "restricted") -> None:
    path = vault / ".constellation/config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["egress"] = {
        "external_enabled": False,
        "providers": {
            PROVIDER: {
                "enabled": True,
                "transport": "local",
                "max_sensitivity": max_sensitivity,
                "models": [MODEL],
                "purposes": ["stage1"],
            }
        },
    }
    path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")


def _fixture(
    tmp_path: Path,
    *,
    authorized: bool = True,
    source_sensitivity: Sensitivity = Sensitivity.INTERNAL,
    max_sensitivity: str = "restricted",
    source_text: str = SOURCE_TEXT,
) -> tuple[Path, Path, str, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    if authorized:
        _declare_model(vault, max_sensitivity=max_sensitivity)

    source_id = generate_ulid()
    source_rel = Path("Library/Files/2026") / source_id / "source.md"
    source_path = vault / source_rel
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source_text, encoding="utf-8")
    source = SourceItem(
        id=source_id,
        type="source_item",
        title="Fictional source",
        status="active",
        sensitivity=source_sensitivity,
        source_hash=hashlib.sha256(source_text.encode()).hexdigest(),
        original_path=source_rel.as_posix(),
        media_type="text/markdown",
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "source-items" / f"{source_id}.md").write_text(
        render_frontmatter(source.model_dump(mode="json", exclude_none=True), "# Source\n"),
        encoding="utf-8",
    )

    subject = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.PERSON,
        title="Alex Example",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[source_id],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "people").mkdir(exist_ok=True)
    (vault / "people" / "person-alex-example.md").write_text(
        render_frontmatter(subject.model_dump(mode="json", exclude_none=True), "# Alex Example\n"),
        encoding="utf-8",
    )
    return vault, source_path, source_id, subject.id


def _extract(vault: Path, source_path: Path, source_id: str, subject_id: str, caller):
    return extract_claims_from_source(
        vault,
        source_path,
        subject_id=subject_id,
        source_ids=[source_id],
        provider=PROVIDER,
        model=MODEL,
        model_caller=caller,
    )


def _single_receipt(vault: Path) -> dict[str, object]:
    paths = list((vault / ".constellation/claim-extractions").glob("*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _candidate(vault: Path, result: dict[str, object]) -> dict[str, object]:
    claim_ids = result["claim_ids"]
    assert isinstance(claim_ids, list)
    path = vault / ".constellation/candidates" / f"claim-{claim_ids[0]}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _research_run(vault: Path, source_hash: str, *, promotion_allowed: bool = True) -> str:
    run_id = generate_ulid()
    run_dir = vault / ".constellation/research-runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / f"{source_hash}.md").write_text(SOURCE_TEXT, encoding="utf-8")
    (run_dir / "receipt.json").write_text(
        json.dumps(
            {
                "status": "completed" if promotion_allowed else "partial",
                "promotion_allowed": promotion_allowed,
                "sources": [{"source_hash": source_hash}],
            }
        ),
        encoding="utf-8",
    )
    return run_id


def _two_chunk_source(*, second_verb: str = "reported") -> tuple[str, str, str]:
    first_evidence = "First Example approved the fictional pilot."
    second_evidence = f"Second Example {second_verb} the fictional result."
    source = (
        first_evidence
        + "\n"
        + ("First paragraph filler.\n" * 200)
        + "\n"
        + second_evidence
        + "\n"
        + ("Second paragraph filler.\n" * 200)
    )
    return source, first_evidence, second_evidence


def test_source_chunking_is_bounded_exact_and_line_anchored() -> None:
    source = (
        ("First paragraph line.\n" * 150)
        + "\n"
        + ("Second paragraph line.\n" * 150)
        + "\n"
        + ("x" * 6_001)
        + "\nFinal line without newline."
    )

    chunks = claim_extractor._chunk_source(source)

    assert "".join(chunk.text for chunk in chunks) == source
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert len(chunks) >= 3
    cursor = 0
    for chunk in chunks:
        assert len(chunk.text) <= 6_000 or "\n" not in chunk.text.rstrip("\n")
        assert chunk.start_line == source.count("\n", 0, cursor) + 1
        expected_end = chunk.start_line + chunk.text.count("\n") - int(chunk.text.endswith("\n"))
        assert chunk.end_line == expected_end
        assert chunk.sha256 == hashlib.sha256(chunk.text.encode()).hexdigest()
        cursor += len(chunk.text)
    assert cursor == len(source)
    assert all(left.end_line < right.start_line for left, right in zip(chunks, chunks[1:]))


def test_source_read_preserves_crlf_bytes_for_hash_and_prompt(tmp_path: Path) -> None:
    source = f"# Fictional source\r\n\r\n{EVIDENCE}\r\n"
    vault, source_path, source_id, subject_id = _fixture(tmp_path, source_text=source)
    prompts: list[str] = []

    def caller(**request):
        prompts.append(str(request["prompt"]))
        return _claim_response()

    result = _extract(vault, source_path, source_id, subject_id, caller)

    assert result["staged"] == 1
    assert prompts == [claim_extractor.EXTRACTION_PROMPT.format(document=source)]
    assert _candidate(vault, result)["evidence_anchor"] == "L000003-L000003"


def test_cr_only_source_uses_consistent_global_line_anchors(tmp_path: Path) -> None:
    source = ("Fictional filler line for chunking.\r" * 200) + EVIDENCE + "\r"
    chunks = claim_extractor._chunk_source(source)
    assert len(chunks) == 2
    assert chunks[1].start_line == chunks[0].text.count("\r") + 1
    vault, source_path, source_id, subject_id = _fixture(tmp_path, source_text=source)

    def caller(**request):
        if EVIDENCE in str(request["prompt"]):
            return _claim_response()
        return {"content": "[]", "provider_request_id": "empty-chunk"}

    result = _extract(vault, source_path, source_id, subject_id, caller)

    assert result["staged"] == 1
    assert _candidate(vault, result)["evidence_anchor"] == "L000201-L000201"


def test_long_source_authorizes_and_extracts_each_exact_chunk(tmp_path: Path) -> None:
    source, first_evidence, second_evidence = _two_chunk_source()
    chunks = claim_extractor._chunk_source(source)
    assert len(chunks) == 2
    vault, source_path, source_id, subject_id = _fixture(tmp_path, source_text=source)
    calls: list[dict[str, object]] = []

    def caller(**request):
        calls.append(request)
        if first_evidence in str(request["prompt"]):
            return _claim_response(
                evidence=first_evidence,
                request_id="chunk-request-1",
                predicate="approved",
                object_literal="fictional pilot",
            )
        return _claim_response(
            evidence=second_evidence,
            request_id="chunk-request-2",
            predicate="reported",
            object_literal="fictional result",
        )

    result = _extract(vault, source_path, source_id, subject_id, caller)

    assert len(calls) == 2
    assert [call["prompt"] for call in calls] == [
        claim_extractor.EXTRACTION_PROMPT.format(document=chunk.text) for chunk in chunks
    ]
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    events = [
        json.loads(line)
        for line in (vault / ".constellation/egress-ledger.jsonl").read_text().splitlines()
    ]
    assert len(events) == 2
    assert all(event["source_hashes"] == [source_hash] for event in events)
    assert len({event["request_input_sha256"] for event in events}) == 2
    candidates = [
        json.loads(path.read_text())
        for path in (vault / ".constellation/candidates").glob("claim-*.json")
    ]
    by_predicate = {candidate["predicate"]: candidate for candidate in candidates}
    assert result["staged"] == 2
    assert by_predicate["approved"]["evidence_anchor"] == "L000001-L000001"
    assert by_predicate["reported"]["evidence_anchor"] == "L000203-L000203"
    assert all(candidate["source_ids"] == [source_id] for candidate in candidates)


def test_later_chunk_failure_stages_nothing_and_records_failed_range(tmp_path: Path) -> None:
    source, first_evidence, _ = _two_chunk_source()
    chunks = claim_extractor._chunk_source(source)
    vault, source_path, source_id, subject_id = _fixture(tmp_path, source_text=source)
    calls = 0

    def caller(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _claim_response(
                evidence=first_evidence,
                object_literal="PRIVATE_RESPONSE_MARKER",
            )
        return {"content": "not-json", "provider_request_id": "chunk-request-2"}

    with pytest.raises(ClaimExtractionError, match="parse model claims"):
        _extract(vault, source_path, source_id, subject_id, caller)

    assert not list((vault / ".constellation/candidates").glob("claim-*.json"))
    receipt_path = next((vault / ".constellation/claim-extractions").glob("*.json"))
    receipt_text = receipt_path.read_text()
    receipt = json.loads(receipt_text)
    assert receipt["status"] == "failed"
    assert receipt["staged"] == 0
    assert receipt["failed_chunk"] == {
        "chunk_index": 1,
        "start_line": chunks[1].start_line,
        "end_line": chunks[1].end_line,
    }
    assert receipt["chunks"][0]["status"] == "complete"
    assert receipt["chunks"][1]["status"] == "failed"
    assert "PRIVATE_RESPONSE_MARKER" not in receipt_text
    assert len((vault / ".constellation/egress-ledger.jsonl").read_text().splitlines()) == 2


def test_schema_invalid_later_chunk_stages_nothing(tmp_path: Path) -> None:
    source, first_evidence, second_evidence = _two_chunk_source()
    vault, source_path, source_id, subject_id = _fixture(tmp_path, source_text=source)
    calls = 0

    def caller(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _claim_response(evidence=first_evidence)
        return _claim_response(evidence=second_evidence, object_literal="x" * 501)

    with pytest.raises(ClaimExtractionError, match="claim failed schema validation"):
        _extract(vault, source_path, source_id, subject_id, caller)

    assert not list((vault / ".constellation/candidates").glob("claim-*.json"))
    receipt = _single_receipt(vault)
    assert receipt["status"] == "failed"
    assert receipt["staged"] == 0
    assert receipt["error"] == "model_response_invalid"


def test_cross_chunk_duplicates_stage_once_with_metadata_only_receipt(tmp_path: Path) -> None:
    source, first_evidence, second_evidence = _two_chunk_source(second_verb="confirmed")
    vault, source_path, source_id, subject_id = _fixture(tmp_path, source_text=source)
    calls = 0

    def caller(**_):
        nonlocal calls
        calls += 1
        evidence = first_evidence if calls == 1 else second_evidence
        response = _claim_response(
            evidence=evidence,
            request_id=f"chunk-request-{calls}",
            predicate="approved",
            object_literal="fictional pilot",
        )
        response["usage"] = {
            "input_tokens": 1,
            "output_tokens": -1,
            "total_tokens": float("inf"),
            "reasoning_tokens": float("nan"),
            "cache_read_tokens": 10**100,
            "PRIVATE_PROVIDER_CONTENT_MARKER": 1,
        }
        return response

    result = _extract(vault, source_path, source_id, subject_id, caller)

    assert result["status"] == "complete"
    assert result["staged"] == 1
    assert result["skipped"] == 1
    assert len(list((vault / ".constellation/candidates").glob("claim-*.json"))) == 1
    receipt_path = next((vault / ".constellation/claim-extractions").glob("*.json"))
    receipt_text = receipt_path.read_text()
    receipt = json.loads(receipt_text)
    assert receipt["chunk_count"] == 2
    assert len(receipt["authorization_ids"]) == 2
    assert len(set(receipt["authorization_ids"])) == 2
    assert all(
        {
            "chunk_index",
            "start_line",
            "end_line",
            "chunk_sha256",
            "request_input_sha256",
            "request_sha256",
            "authorization_id",
            "provider_request_id_sha256",
            "response_sha256",
            "usage",
            "status",
        }
        <= set(chunk)
        for chunk in receipt["chunks"]
    )
    assert first_evidence not in receipt_text
    assert second_evidence not in receipt_text
    assert "PRIVATE_PROVIDER_CONTENT_MARKER" not in receipt_text
    assert "Infinity" not in receipt_text
    assert "NaN" not in receipt_text
    assert all(chunk["usage"] == {"input_tokens": 1} for chunk in receipt["chunks"])


def test_existing_staged_candidate_is_deduplicated(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    stage_claim(
        vault,
        subject_id=subject_id,
        predicate="appointed_as",
        object_literal="chief tester",
        source_ids=[source_id],
        evidence_anchor="L000003-L000003",
        evidence_excerpt=EVIDENCE,
        observed_at=NOW,
    )

    result = _extract(
        vault,
        source_path,
        source_id,
        subject_id,
        lambda **_: _claim_response(),
    )

    assert result["status"] == "no_claims"
    assert result["staged"] == 0
    assert result["skipped"] == 1
    assert len(list((vault / ".constellation/candidates").glob("claim-*.json"))) == 1


def test_existing_candidate_for_another_subject_does_not_deduplicate(tmp_path: Path) -> None:
    vault, source_path, source_id, first_subject_id = _fixture(tmp_path)
    stage_claim(
        vault,
        subject_id=first_subject_id,
        predicate="appointed_as",
        object_literal="chief tester",
        source_ids=[source_id],
        evidence_anchor="L000003-L000003",
        evidence_excerpt=EVIDENCE,
        observed_at=NOW,
    )
    second_subject = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.PERSON,
        title="Jordan Example",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_ids=[source_id],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "people" / "person-jordan-example.md").write_text(
        render_frontmatter(
            second_subject.model_dump(mode="json", exclude_none=True),
            "# Jordan Example\n",
        ),
        encoding="utf-8",
    )

    result = _extract(
        vault,
        source_path,
        source_id,
        second_subject.id,
        lambda **_: _claim_response(),
    )

    assert result["status"] == "complete"
    assert result["staged"] == 1
    assert result["skipped"] == 0
    assert len(list((vault / ".constellation/candidates").glob("claim-*.json"))) == 2


def test_denied_egress_records_receipt_before_model_call(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path, authorized=False)
    with pytest.raises(EgressDenied, match="provider_not_declared"):
        _extract(
            vault,
            source_path,
            source_id,
            subject_id,
            lambda **_: pytest.fail("model must not run before authorization"),
        )

    event = json.loads((vault / ".constellation/egress-ledger.jsonl").read_text().splitlines()[0])
    assert event["allowed"] is False
    assert _single_receipt(vault)["status"] == "denied"


def test_authorized_call_stages_anchored_claim_and_receipt(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    calls: list[dict[str, object]] = []

    def caller(**request):
        calls.append(request)
        return _claim_response()

    result = _extract(vault, source_path, source_id, subject_id, caller)

    assert result["status"] == "complete"
    assert calls[0]["provider"] == PROVIDER
    assert calls[0]["model"] == MODEL
    candidate = _candidate(vault, result)
    assert candidate["source_ids"] == [source_id]
    assert candidate["evidence_anchor"] == "L000003-L000003"
    receipt = _single_receipt(vault)
    assert receipt["status"] == "complete"
    assert "provider_request_id" not in receipt
    assert receipt["provider_request_id_sha256"] == hashlib.sha256(b"request-1").hexdigest()
    assert receipt["response_sha256"]


def test_model_failure_writes_failed_receipt_without_provider_text(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    provider_marker = "PRIVATE_PROVIDER_ERROR_MARKER"

    def caller(**_):
        raise RuntimeError(provider_marker)

    with pytest.raises(ClaimExtractionError, match="model call failed"):
        _extract(vault, source_path, source_id, subject_id, caller)

    receipt_path = next((vault / ".constellation/claim-extractions").glob("*.json"))
    receipt_text = receipt_path.read_text()
    receipt = json.loads(receipt_text)
    assert receipt["status"] == "failed"
    assert receipt["error"] == "model_call_failed"
    assert provider_marker not in receipt_text


def test_strict_json_markdown_fence_is_accepted(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    raw_json = _claim_response()["content"]

    result = _extract(
        vault,
        source_path,
        source_id,
        subject_id,
        lambda **_: {"content": f"```json\n{raw_json}\n```"},
    )

    assert result["staged"] == 1
    assert _single_receipt(vault)["status"] == "complete"


def test_embedded_array_text_inside_claim_does_not_create_a_second_payload() -> None:
    payload = [
        {
            "predicate": "reported_values",
            "object_literal": "recorded values",
            "evidence_excerpt": "Values [1, 2] were recorded.",
            "confidence": "direct_quote",
        }
    ]
    wrapped = f"```json\n{json.dumps(payload)}\n```"

    assert claim_extractor._load_claim_payload(wrapped) == payload


def test_unique_embedded_json_array_is_accepted(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    raw_json = _claim_response()["content"]

    result = _extract(
        vault,
        source_path,
        source_id,
        subject_id,
        lambda **_: {"content": f"Here are the claims:\n{raw_json}\nEnd."},
    )

    assert result["staged"] == 1
    assert _single_receipt(vault)["status"] == "complete"


def test_malformed_model_output_fails_closed_with_receipt(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    with pytest.raises(ClaimExtractionError, match="parse model claims"):
        _extract(
            vault,
            source_path,
            source_id,
            subject_id,
            lambda **_: {"content": "not-json"},
        )

    assert not list((vault / ".constellation/candidates").glob("claim-*.json"))
    assert _single_receipt(vault)["status"] == "failed"


def test_unanchored_claim_fails_closed_without_candidate(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    with pytest.raises(ClaimExtractionError, match="not found exactly"):
        _extract(
            vault,
            source_path,
            source_id,
            subject_id,
            lambda **_: _claim_response(evidence="Fabricated evidence."),
        )

    assert not list((vault / ".constellation/candidates").glob("claim-*.json"))
    assert _single_receipt(vault)["status"] == "failed"


def test_egress_uses_canonical_source_sensitivity_not_caller_value(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(
        tmp_path,
        source_sensitivity=Sensitivity.RESTRICTED,
        max_sensitivity="internal",
    )

    with pytest.raises(EgressDenied, match="sensitivity"):
        _extract(
            vault,
            source_path,
            source_id,
            subject_id,
            lambda **_: pytest.fail("model must not run"),
        )

    event = json.loads((vault / ".constellation/egress-ledger.jsonl").read_text().splitlines()[0])
    assert event["sensitivity"] == "restricted"


def test_subject_must_be_canonical_before_egress(tmp_path: Path) -> None:
    vault, source_path, source_id, _ = _fixture(tmp_path)

    with pytest.raises(ClaimExtractionError, match="canonical subject"):
        _extract(
            vault,
            source_path,
            source_id,
            generate_ulid(),
            lambda **_: pytest.fail("model must not run"),
        )

    assert not (vault / ".constellation/egress-ledger.jsonl").exists()
    assert _single_receipt(vault)["status"] == "failed"


def test_source_id_must_match_preserved_hash_before_egress(tmp_path: Path) -> None:
    vault, source_path, _, subject_id = _fixture(tmp_path)
    with pytest.raises(ClaimExtractionError, match="canonical source item"):
        _extract(
            vault,
            source_path,
            generate_ulid(),
            subject_id,
            lambda **_: pytest.fail("model must not run"),
        )

    assert not (vault / ".constellation/egress-ledger.jsonl").exists()
    assert _single_receipt(vault)["status"] == "failed"


def test_run_resolves_source_hash_to_canonical_source_id(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    source_hash = hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()
    run_id = _research_run(vault, source_hash)

    result = extract_claims_from_run(
        vault,
        run_id,
        subject_id=subject_id,
        provider=PROVIDER,
        model=MODEL,
        model_caller=lambda **_: _claim_response(),
    )

    assert _candidate(vault, result)["source_ids"] == [source_id]
    assert result["sources_processed"] == 1
    assert source_path.is_file()


@pytest.mark.parametrize(
    ("second_source_bytes", "error_match"),
    [
        (b"\xff", "valid UTF-8"),
        (b" \n\t", "empty"),
    ],
)
def test_run_preflights_every_source_before_first_model_call(
    tmp_path: Path, second_source_bytes: bytes, error_match: str
) -> None:
    vault, _, _, subject_id = _fixture(tmp_path)
    first_hash = hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()
    second_hash = hashlib.sha256(second_source_bytes).hexdigest()
    second_source_id = generate_ulid()
    second_source = SourceItem(
        id=second_source_id,
        type="source_item",
        title="Second fictional source",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_hash=second_hash,
        original_path=f"Library/Files/2026/{second_source_id}/source.md",
        media_type="text/markdown",
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "source-items" / f"{second_source_id}.md").write_text(
        render_frontmatter(second_source.model_dump(mode="json", exclude_none=True), "# Source\n"),
        encoding="utf-8",
    )
    run_id = generate_ulid()
    run_dir = vault / ".constellation/research-runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / f"{first_hash}.md").write_text(SOURCE_TEXT, encoding="utf-8")
    (run_dir / f"{second_hash}.md").write_bytes(second_source_bytes)
    (run_dir / "receipt.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "promotion_allowed": True,
                "sources": [
                    {"source_hash": first_hash},
                    {"source_hash": second_hash},
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def caller(**_):
        nonlocal calls
        calls += 1
        return _claim_response()

    with pytest.raises(ClaimExtractionError, match=error_match):
        extract_claims_from_run(
            vault,
            run_id,
            subject_id=subject_id,
            provider=PROVIDER,
            model=MODEL,
            model_caller=caller,
        )

    assert calls == 0
    assert not list((vault / ".constellation/candidates").glob("claim-*.json"))
    assert not (vault / ".constellation/egress-ledger.jsonl").exists()
    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (vault / ".constellation/claim-extractions").glob("*.json")
    ]
    assert len(receipts) == 2
    by_source_hash = {receipt["source_hash"]: receipt for receipt in receipts}
    assert by_source_hash[first_hash]["status"] == "failed"
    assert by_source_hash[first_hash]["error"] == "run_preflight_aborted"
    assert by_source_hash[second_hash]["status"] == "failed"
    assert by_source_hash[second_hash]["error"] == "preflight_failed"
    assert all(receipt["staged"] == 0 for receipt in receipts)


def test_run_static_preflight_failure_writes_terminal_receipt(tmp_path: Path) -> None:
    vault, _, _, _ = _fixture(tmp_path)
    source_hash = hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()
    run_id = _research_run(vault, source_hash)

    with pytest.raises(ClaimExtractionError, match="canonical subject"):
        extract_claims_from_run(
            vault,
            run_id,
            subject_id=generate_ulid(),
            provider=PROVIDER,
            model=MODEL,
            model_caller=lambda **_: pytest.fail("model must not run"),
        )

    receipt = _single_receipt(vault)
    assert receipt["status"] == "failed"
    assert receipt["source_hash"] == source_hash
    assert receipt["error"] == "preflight_failed"
    assert receipt["staged"] == 0
    assert not (vault / ".constellation/egress-ledger.jsonl").exists()


def test_run_fails_if_receipt_source_is_not_preserved(tmp_path: Path) -> None:
    vault, _, _, subject_id = _fixture(tmp_path)
    source_hash = hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()
    run_id = _research_run(vault, source_hash)
    (vault / ".constellation/research-runs" / run_id / f"{source_hash}.md").unlink()

    with pytest.raises(ClaimExtractionError, match="preserved source is missing"):
        extract_claims_from_run(
            vault,
            run_id,
            subject_id=subject_id,
            provider=PROVIDER,
            model=MODEL,
            model_caller=lambda **_: pytest.fail("model must not run"),
        )


def test_run_rejects_nonpromotable_research_receipt(tmp_path: Path) -> None:
    vault, _, _, subject_id = _fixture(tmp_path)
    source_hash = hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()
    run_id = _research_run(vault, source_hash, promotion_allowed=False)

    with pytest.raises(ClaimExtractionError, match="not promotion-allowed"):
        extract_claims_from_run(
            vault,
            run_id,
            subject_id=subject_id,
            provider=PROVIDER,
            model=MODEL,
            model_caller=lambda **_: pytest.fail("model must not run"),
        )


def test_default_transport_uses_generic_endpoint_and_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    endpoint = "https://models.example.test/v1/chat/completions"
    monkeypatch.setenv("CONSTELLATION_MODEL_ENDPOINT", endpoint)
    monkeypatch.setenv("CONSTELLATION_MODEL_API_KEY", "fictional-test-key")
    monkeypatch.setenv("CONSTELLATION_MODEL_REASONING_ENABLED", "false")
    monkeypatch.setenv("CONSTELLATION_MODEL_TIMEOUT_SECONDS", "180")
    monkeypatch.delenv("CONSTELLATION_MODEL_MAX_TOKENS", raising=False)
    requests: list[Any] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, size=-1):
            assert size == 1_000_001
            content = _claim_response()["content"]
            return json.dumps(
                {"id": "generic-1", "choices": [{"message": {"content": content}}]}
            ).encode()

    def urlopen(request, timeout):
        requests.append(request)
        assert timeout == 180
        return Response()

    monkeypatch.setattr("constellation.claim_extractor.urllib.request.urlopen", urlopen)
    result = extract_claims_from_source(
        vault,
        source_path,
        subject_id=subject_id,
        source_ids=[source_id],
        provider=PROVIDER,
        model=MODEL,
    )

    assert result["staged"] == 1
    request = requests[0]
    assert request.full_url == endpoint
    assert request.get_header("Authorization") == "Bearer fictional-test-key"
    payload = json.loads(request.data)
    assert payload["model"] == MODEL
    assert payload["max_tokens"] == 4_000
    assert payload["reasoning"] == {"enabled": False}
    assert "response_format" not in payload


def test_model_max_tokens_override_is_passed_to_injected_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    monkeypatch.setenv("CONSTELLATION_MODEL_MAX_TOKENS", "8000")
    observed: list[int] = []

    def caller(**request):
        observed.append(request["max_tokens"])
        return _claim_response()

    result = _extract(vault, source_path, source_id, subject_id, caller)

    assert result["staged"] == 1
    assert observed == [8000]


@pytest.mark.parametrize("value", ["0", "16001", "not-an-integer"])
def test_invalid_model_max_tokens_fails_before_egress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)
    monkeypatch.setenv("CONSTELLATION_MODEL_MAX_TOKENS", value)

    with pytest.raises(
        ClaimExtractionError,
        match="CONSTELLATION_MODEL_MAX_TOKENS must be an integer from 1 to 16000",
    ):
        _extract(
            vault,
            source_path,
            source_id,
            subject_id,
            lambda **_: pytest.fail("model must not run"),
        )

    assert not (vault / ".constellation/egress-ledger.jsonl").exists()
    receipt = _single_receipt(vault)
    assert receipt["status"] == "failed"
    assert receipt["error"] == "preflight_failed"
