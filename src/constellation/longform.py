"""Hierarchical long-form document maps without whole-document prompting."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PAGE = re.compile(r"^\[P(\d{4})]$")


def _estimate_tokens(text: str) -> int:
    # Conservative local estimate: ~4 characters per token.
    return max(1, (len(text) + 3) // 4)


def build_document_map(*, source_id: str, text: str) -> dict[str, Any]:
    """Build a review-only hierarchical map from headings/pages in extracted text."""
    nodes: list[dict[str, Any]] = []
    title: str | None = None
    current_page: int | None = None
    line_no = 0
    for raw in text.splitlines():
        line_no += 1
        stripped = raw.strip()
        if not stripped:
            continue
        page = _PAGE.fullmatch(stripped)
        if page:
            current_page = int(page.group(1))
            continue
        heading = _HEADING.fullmatch(stripped)
        if heading:
            level = len(heading.group(1))
            heading_title = heading.group(2).strip()
            if title is None and level == 1:
                title = heading_title
            nodes.append(
                {
                    "index": len(nodes) + 1,
                    "level": level,
                    "title": heading_title,
                    "anchor": f"H{len(nodes) + 1:04d}:L{line_no:04d}",
                    "page": current_page,
                }
            )
            continue
        if not nodes:
            # Body before any heading becomes an implicit root section.
            title = title or "Untitled document"
            nodes.append(
                {
                    "index": 1,
                    "level": 1,
                    "title": title,
                    "anchor": f"H0001:L{line_no:04d}",
                    "page": current_page,
                }
            )
    if not nodes:
        title = "Untitled document"
        nodes.append(
            {
                "index": 1,
                "level": 1,
                "title": title,
                "anchor": "H0001:L0001",
                "page": None,
            }
        )
    return {
        "version": 1,
        "status": "review-required",
        "source_id": source_id,
        "title": title or nodes[0]["title"],
        "nodes": nodes,
        "whole_document_prompt_allowed": False,
    }


def segment_document(
    *,
    document_map: dict[str, Any],
    text: str,
    target_tokens: int = 2000,
) -> dict[str, Any]:
    """Split long-form text into stable, bounded segments with source anchors."""
    if target_tokens < 50:
        raise ValueError("target_tokens must be at least 50")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()] if text.strip() else [""]

    segments: list[dict[str, Any]] = []
    bucket: list[str] = []
    bucket_tokens = 0
    max_tokens = int(target_tokens * 1.25)
    node_titles = [str(node.get("title", "")) for node in document_map.get("nodes") or []]
    node_index = 0

    def flush() -> None:
        nonlocal bucket, bucket_tokens
        if not bucket:
            return
        body = "\n\n".join(bucket).strip()
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        segment_id = digest[:16]
        anchor_node = (
            document_map["nodes"][min(node_index, len(document_map["nodes"]) - 1)]
            if document_map.get("nodes")
            else {"anchor": f"S{len(segments) + 1:04d}"}
        )
        segments.append(
            {
                "segment_id": segment_id,
                "index": len(segments) + 1,
                "anchor": str(anchor_node.get("anchor", f"S{len(segments) + 1:04d}")),
                "title": (
                    node_titles[min(node_index, len(node_titles) - 1)] if node_titles else None
                ),
                "text": body,
                "text_sha256": digest,
                "estimated_tokens": _estimate_tokens(body),
            }
        )
        bucket = []
        bucket_tokens = 0

    for paragraph in paragraphs:
        heading = _HEADING.fullmatch(paragraph.splitlines()[0].strip()) if paragraph else None
        if heading and bucket:
            flush()
            if node_index + 1 < len(node_titles):
                node_index += 1
        # Hard-split oversized paragraphs so a single block cannot exceed the bound.
        pieces: list[str] = [paragraph]
        if _estimate_tokens(paragraph) > max_tokens:
            words = paragraph.split()
            pieces = []
            chunk: list[str] = []
            chunk_tokens = 0
            for word in words:
                word_tokens = _estimate_tokens(word + " ")
                if chunk and chunk_tokens + word_tokens > target_tokens:
                    pieces.append(" ".join(chunk))
                    chunk = []
                    chunk_tokens = 0
                chunk.append(word)
                chunk_tokens += word_tokens
            if chunk:
                pieces.append(" ".join(chunk))
        for piece in pieces:
            tokens = _estimate_tokens(piece)
            if bucket and bucket_tokens + tokens > max_tokens:
                flush()
            bucket.append(piece)
            bucket_tokens += tokens
            if bucket_tokens >= target_tokens:
                flush()
    flush()

    return {
        "version": 1,
        "status": "review-required",
        "source_id": document_map.get("source_id"),
        "target_tokens": target_tokens,
        "whole_document_prompt_allowed": False,
        "segments": segments,
    }
