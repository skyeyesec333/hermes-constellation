"""Bounded Gmail local-capture contract with no network client and no send path."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

_EMAILISH = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MESSAGE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")


class GmailCaptureError(RuntimeError):
    """Raised when a Gmail capture document is unsafe or incomplete."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GmailCaptureError(f"gmail capture requires non-empty {key}")
    return value.strip()


def _normalize_attachment(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise GmailCaptureError("attachment refs must be objects")
    filename = str(item.get("filename") or "").strip()
    mime_type = str(item.get("mime_type") or "application/octet-stream").strip()
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise GmailCaptureError("attachment filename is unsafe")
    size = item.get("size", 0)
    if not isinstance(size, int) or size < 0:
        raise GmailCaptureError("attachment size must be a non-negative integer")
    return {
        "filename": filename,
        "mime_type": mime_type,
        "size": size,
        **({"attachment_id": str(item["attachment_id"])} if item.get("attachment_id") else {}),
    }


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise GmailCaptureError("messages must be objects")
    forbidden = {"raw", "oauth", "token", "access_token", "refresh_token", "password", "credentials"}
    if forbidden.intersection(message):
        raise GmailCaptureError("gmail capture must not include credential or raw transport fields")
    message_id = str(message.get("message_id") or "").strip()
    thread_id = str(message.get("thread_id") or "").strip()
    if not _MESSAGE_ID.fullmatch(message_id) or not _MESSAGE_ID.fullmatch(thread_id):
        raise GmailCaptureError("message_id and thread_id must be present and safe")
    headers = message.get("headers") or {}
    if not isinstance(headers, dict):
        raise GmailCaptureError("message headers must be an object")
    normalized_headers = {
        key: str(value).strip()
        for key, value in headers.items()
        if isinstance(key, str) and str(value).strip()
    }
    for required in ("from", "subject", "date"):
        if required not in normalized_headers:
            raise GmailCaptureError(f"message headers require {required}")
    body_text = str(message.get("body_text") or "")
    attachments = [_normalize_attachment(item) for item in (message.get("attachment_refs") or [])]
    content_key = json.dumps(
        {
            "message_id": message_id,
            "thread_id": thread_id,
            "headers": normalized_headers,
            "body_text": body_text,
            "attachment_refs": attachments,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "headers": normalized_headers,
        "body_text": body_text,
        "attachment_refs": attachments,
        "content_sha256": _sha256_text(content_key),
    }


def dedupe_gmail_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by Gmail message ID, then by content hash for identical re-captures."""
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    result: list[dict[str, Any]] = []
    for message in messages:
        normalized = _normalize_message(message)
        if normalized["message_id"] in seen_ids or normalized["content_sha256"] in seen_hashes:
            continue
        seen_ids.add(normalized["message_id"])
        seen_hashes.add(normalized["content_sha256"])
        result.append(normalized)
    return result


def build_gmail_capture(
    *,
    account_alias: str,
    query: str,
    queried_at: datetime,
    max_results: int,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a local Gmail capture document. Never accepts credentials or send flags."""
    account_alias = account_alias.strip()
    query = query.strip()
    if not account_alias or not _EMAILISH.fullmatch(account_alias):
        raise GmailCaptureError("account_alias must look like an email alias")
    if not query:
        raise GmailCaptureError("gmail capture requires an explicit query")
    if max_results < 1 or max_results > 50:
        raise GmailCaptureError("max_results must be between 1 and 50")
    if queried_at.tzinfo is None or queried_at.utcoffset() is None:
        raise GmailCaptureError("queried_at must include a timezone")
    if not isinstance(messages, list):
        raise GmailCaptureError("messages must be a list")
    if len(messages) > max_results:
        raise GmailCaptureError("messages exceed max_results bound")

    capture = {
        "version": 1,
        "kind": "gmail-capture",
        "account_alias": account_alias,
        "query": query,
        "queried_at": queried_at.isoformat(),
        "max_results": max_results,
        "messages": dedupe_gmail_messages(messages),
        "receipt": {
            "connector": "gmail-constellation-capture",
            "version": "1",
            "credentials_boundary": "hermes-google-workspace; never stored in constellation",
            "send_enabled": False,
            "bulk_sync": False,
        },
    }
    return validate_gmail_capture(capture)


def validate_gmail_capture(capture: dict[str, Any]) -> dict[str, Any]:
    """Validate a local Gmail capture; fails closed on send/bulk/credential fields."""
    if not isinstance(capture, dict):
        raise GmailCaptureError("gmail capture must be an object")
    if capture.get("kind") != "gmail-capture":
        raise GmailCaptureError("capture kind must be gmail-capture")
    if int(capture.get("version") or 0) != 1:
        raise GmailCaptureError("unsupported gmail capture version")
    account_alias = _require_str(capture, "account_alias")
    if not _EMAILISH.fullmatch(account_alias):
        raise GmailCaptureError("account_alias must look like an email alias")
    query = _require_str(capture, "query")
    queried_at = _require_str(capture, "queried_at")
    max_results = capture.get("max_results")
    if not isinstance(max_results, int) or max_results < 1 or max_results > 50:
        raise GmailCaptureError("max_results must be between 1 and 50")
    messages = capture.get("messages")
    if not isinstance(messages, list):
        raise GmailCaptureError("messages must be a list")
    if len(messages) > max_results:
        raise GmailCaptureError("messages exceed max_results bound")
    receipt = capture.get("receipt")
    if not isinstance(receipt, dict):
        raise GmailCaptureError("receipt is required")
    if receipt.get("send_enabled") is not False:
        raise GmailCaptureError("gmail capture must not enable send")
    if receipt.get("bulk_sync") is not False:
        raise GmailCaptureError("gmail capture must not enable bulk sync")
    if "credentials" in receipt or "token" in receipt or "oauth" in receipt:
        raise GmailCaptureError("receipt must not include credentials")
    if str(receipt.get("connector") or "") != "gmail-constellation-capture":
        raise GmailCaptureError("receipt connector must be gmail-constellation-capture")

    normalized_messages = dedupe_gmail_messages(messages)
    return {
        "version": 1,
        "kind": "gmail-capture",
        "account_alias": account_alias,
        "query": query,
        "queried_at": queried_at,
        "max_results": max_results,
        "messages": normalized_messages,
        "receipt": {
            "connector": "gmail-constellation-capture",
            "version": str(receipt.get("version") or "1"),
            "credentials_boundary": str(
                receipt.get("credentials_boundary")
                or "hermes-google-workspace; never stored in constellation"
            ),
            "send_enabled": False,
            "bulk_sync": False,
        },
    }


def render_gmail_capture_text(capture: dict[str, Any]) -> str:
    """Render a stable, reviewable text form for ingest/preservation."""
    validated = validate_gmail_capture(capture)
    lines = [
        f"# Gmail capture: {validated['query']}",
        "",
        f"Account alias: {validated['account_alias']}",
        f"Queried at: {validated['queried_at']}",
        f"Max results: {validated['max_results']}",
        f"Messages: {len(validated['messages'])}",
        "Send enabled: false",
        "Bulk sync: false",
        "",
    ]
    for index, message in enumerate(validated["messages"], start=1):
        headers = message["headers"]
        lines.extend(
            [
                f"## Message {index}",
                f"Message-ID: {message['message_id']}",
                f"Thread-ID: {message['thread_id']}",
                f"From: {headers.get('from', '')}",
                f"To: {headers.get('to', '')}",
                f"Subject: {headers.get('subject', '')}",
                f"Date: {headers.get('date', '')}",
                f"Content-SHA256: {message['content_sha256']}",
                "",
                message["body_text"].strip(),
                "",
            ]
        )
        if message["attachment_refs"]:
            lines.append("Attachments:")
            for attachment in message["attachment_refs"]:
                lines.append(
                    f"- {attachment['filename']} ({attachment['mime_type']}, {attachment['size']} bytes)"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
