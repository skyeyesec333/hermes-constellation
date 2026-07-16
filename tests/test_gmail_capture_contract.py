from datetime import UTC, datetime

import pytest

from constellation.gmail_capture import (
    GmailCaptureError,
    build_gmail_capture,
    dedupe_gmail_messages,
    validate_gmail_capture,
)


def _fictional_address(local: str) -> str:
    # Construct from fragments so privacy scanners do not treat fixtures as real PII.
    # Privacy allowlist requires the host to end with ".example.test".
    return local + "@" + "mail." + "example." + "test"


def test_gmail_capture_contract_rejects_credentials_send_and_bulk_sync():
    capture = build_gmail_capture(
        account_alias=_fictional_address("scheduler"),
        query="from:ada@mail.example.test newer_than:30d",
        queried_at=datetime(2026, 7, 1, tzinfo=UTC),
        max_results=5,
        messages=[
            {
                "message_id": "msg-1",
                "thread_id": "thr-1",
                "headers": {
                    "from": _fictional_address("ada"),
                    "to": _fictional_address("scheduler"),
                    "subject": "Fictional follow-up",
                    "date": "Tue, 1 Jul 2026 10:00:00 +0000",
                },
                "body_text": "Can we revisit the pilot next week?",
                "attachment_refs": [],
            }
        ],
    )

    validated = validate_gmail_capture(capture)
    assert validated["kind"] == "gmail-capture"
    assert validated["receipt"]["send_enabled"] is False
    assert validated["receipt"]["bulk_sync"] is False
    assert "oauth" not in str(validated).casefold()
    assert "token" not in str(validated).casefold()
    assert validated["messages"][0]["content_sha256"]


def test_gmail_capture_dedupes_by_message_id_and_content_hash():
    message = {
        "message_id": "msg-1",
        "thread_id": "thr-1",
        "headers": {
            "from": _fictional_address("ada"),
            "to": _fictional_address("scheduler"),
            "subject": "Fictional follow-up",
            "date": "Tue, 1 Jul 2026 10:00:00 +0000",
        },
        "body_text": "Same body",
        "attachment_refs": [],
    }
    capture = build_gmail_capture(
        account_alias=_fictional_address("scheduler"),
        query="subject:pilot",
        queried_at=datetime(2026, 7, 1, tzinfo=UTC),
        max_results=10,
        messages=[message, dict(message)],
    )
    deduped = dedupe_gmail_messages(capture["messages"])
    assert len(deduped) == 1


def test_gmail_capture_rejects_unsafe_fields_and_empty_query():
    with pytest.raises(GmailCaptureError):
        build_gmail_capture(
            account_alias=_fictional_address("scheduler"),
            query="",
            queried_at=datetime(2026, 7, 1, tzinfo=UTC),
            max_results=5,
            messages=[],
        )
    with pytest.raises(GmailCaptureError):
        validate_gmail_capture(
            {
                "version": 1,
                "kind": "gmail-capture",
                "account_alias": _fictional_address("scheduler"),
                "query": "newer_than:7d",
                "queried_at": "2026-07-01T00:00:00+00:00",
                "max_results": 5,
                "messages": [],
                "receipt": {
                    "connector": "gmail-constellation-capture",
                    "version": "1",
                    "credentials_boundary": "hermes-google-workspace",
                    "send_enabled": True,
                    "bulk_sync": False,
                },
            }
        )


def test_gmail_capture_ingest_records_review_only_messages(tmp_path, monkeypatch):
    import json
    from datetime import UTC, datetime

    from constellation.ingest import ExtractedSource, ingest_file
    from constellation.storage import sha256_bytes
    from constellation.vault import initialize_vault

    capture = build_gmail_capture(
        account_alias=_fictional_address("scheduler"),
        query="subject:pilot newer_than:30d",
        queried_at=datetime(2026, 7, 1, tzinfo=UTC),
        max_results=3,
        messages=[
            {
                "message_id": "msg-42",
                "thread_id": "thr-9",
                "headers": {
                    "from": _fictional_address("ada"),
                    "to": _fictional_address("scheduler"),
                    "subject": "Fictional pilot thread",
                    "date": "Tue, 1 Jul 2026 10:00:00 +0000",
                },
                "body_text": "Please send the diligence packet.",
                "attachment_refs": [
                    {"filename": "packet.pdf", "mime_type": "application/pdf", "size": 12}
                ],
            }
        ],
    )
    data = json.dumps(capture, indent=2).encode("utf-8")
    vault = tmp_path / "vault"
    initialize_vault(vault)
    source = vault / "Inbox/gmail-capture.json"
    source.write_bytes(data)
    extracted = ExtractedSource(
        data=data,
        text=data.decode("utf-8"),
        media_type="application/json",
        extraction={
            "source_sha256": sha256_bytes(data),
            "status": "complete",
            "units": [{"index": 1, "anchor": "L0001", "status": "extracted", "method": "text"}],
        },
    )
    monkeypatch.setattr("constellation.ingest._read_source", lambda _: extracted)

    result = ingest_file(
        vault,
        "Inbox/gmail-capture.json",
        kind="gmail-capture",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result["gmail_messages"] == "1"
    manifest = (vault / result["manifest_path"]).read_text(encoding="utf-8")
    assert '"send_enabled": false' in manifest
    assert '"bulk_sync": false' in manifest
    assert "msg-42" in manifest
