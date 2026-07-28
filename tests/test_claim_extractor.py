import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

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


def _claim_response(*, evidence: str = EVIDENCE, request_id: str = "request-1") -> dict[str, object]:
    content = json.dumps(
        [
            {
                "predicate": "appointed_as",
                "object_literal": "chief tester",
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
) -> tuple[Path, Path, str, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    if authorized:
        _declare_model(vault, max_sensitivity=max_sensitivity)

    source_id = generate_ulid()
    source_rel = Path("Library/Files/2026") / source_id / "source.md"
    source_path = vault / source_rel
    source_path.parent.mkdir(parents=True)
    source_path.write_text(SOURCE_TEXT, encoding="utf-8")
    source = SourceItem(
        id=source_id,
        type="source_item",
        title="Fictional source",
        status="active",
        sensitivity=source_sensitivity,
        source_hash=hashlib.sha256(SOURCE_TEXT.encode()).hexdigest(),
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
    assert receipt["provider_request_id"] == "request-1"
    assert receipt["response_sha256"]


def test_model_failure_writes_failed_receipt(tmp_path: Path) -> None:
    vault, source_path, source_id, subject_id = _fixture(tmp_path)

    def caller(**_):
        raise RuntimeError("provider unavailable")

    with pytest.raises(ClaimExtractionError, match="model call failed"):
        _extract(vault, source_path, source_id, subject_id, caller)

    receipt = _single_receipt(vault)
    assert receipt["status"] == "failed"
    assert "provider unavailable" in str(receipt["error"])


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
    assert payload["max_tokens"] == 8_192
    assert "response_format" not in payload
