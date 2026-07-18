"""Tests for book intelligence with canonical source anchors."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from constellation.book_intelligence import (
    BookIntelligenceError,
    _chunk_text,
    _identity_embedding,
    book_status,
    delete_book,
    ingest_book,
    rebuild_books,
    search_books,
)
from constellation.frontmatter import render_frontmatter
from constellation.models import Sensitivity, SourceItem, generate_ulid
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)

SOURCE_TEXT = """# Fictional Book

## Chapter 1: Beginnings

This is the first chapter of the fictional book. It contains important information
about the origins of the subject matter.

## Chapter 2: Development

The development phase brought many changes. Key innovations emerged during this period
that would shape the future direction.

## Chapter 3: Conclusion

The final chapter wraps up the narrative. All the threads come together in a
satisfying resolution that ties back to the themes introduced earlier.
"""


def _setup_vault(tmp_path: Path) -> tuple[Path, str, Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    source_id = generate_ulid()
    source_hash = hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()

    source_item = SourceItem(
        id=source_id,
        type="source_item",
        title="Fictional Book",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_hash=source_hash,
        original_path="Library/Files/2026/fictional-book.md",
        media_type="text/markdown",
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "source-items" / f"{source_id}.md").write_text(
        render_frontmatter(source_item.model_dump(mode="json", exclude_none=True), "# Source\n"),
        encoding="utf-8",
    )

    src_path = vault / "Library/Files/2026/fictional-book.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.write_text(SOURCE_TEXT, encoding="utf-8")

    return vault, source_id, src_path, source_hash


# ── Chunking ────────────────────────────────────────────────────────────


def test_chunks_have_deterministic_ids() -> None:
    source_id = generate_ulid()
    source_hash = "a" * 64
    chunks = _chunk_text("Paragraph one.\n\nParagraph two.", source_id=source_id, source_hash=source_hash)
    assert len(chunks) >= 1
    for c in chunks:
        assert c.chunk_id.startswith(source_hash[:16])
        assert c.source_id == source_id
        assert c.source_hash == source_hash
        assert len(c.chunk_hash) == 64


def test_chunks_are_stable_across_calls() -> None:
    source_id = generate_ulid()
    source_hash = "b" * 64
    text = "Same text.\n\nSame chunks.\n\nEvery time."
    c1 = _chunk_text(text, source_id=source_id, source_hash=source_hash)
    c2 = _chunk_text(text, source_id=source_id, source_hash=source_hash)
    assert [c.chunk_id for c in c1] == [c.chunk_id for c in c2]
    assert [c.chunk_hash for c in c1] == [c.chunk_hash for c in c2]


# ── Ingest ──────────────────────────────────────────────────────────────


def test_ingest_requires_canonical_source(tmp_path: Path) -> None:
    vault, _, src_path, _ = _setup_vault(tmp_path)
    with pytest.raises(BookIntelligenceError, match="canonical source"):
        ingest_book(vault, src_path, source_id="nonexistent", embed_fn=_identity_embedding)


def test_ingest_rejects_hash_mismatch(tmp_path: Path) -> None:
    vault, source_id, src_path, _ = _setup_vault(tmp_path)
    # Tamper with the source
    src_path.write_text("Modified content!", encoding="utf-8")
    with pytest.raises(BookIntelligenceError, match="hash does not match"):
        ingest_book(vault, src_path, source_id=source_id, embed_fn=_identity_embedding)


def test_ingest_indexes_with_fake_embedding(tmp_path: Path) -> None:
    vault, source_id, src_path, _ = _setup_vault(tmp_path)

    result = ingest_book(vault, src_path, source_id=source_id, embed_fn=_identity_embedding)
    assert result["status"] == "indexed"
    assert result["source_id"] == source_id
    assert result["chunk_count"] >= 1


# ── Status ──────────────────────────────────────────────────────────────


def test_status_reports_chunks_after_ingest(tmp_path: Path) -> None:
    vault, source_id, src_path, _ = _setup_vault(tmp_path)
    ingest_book(vault, src_path, source_id=source_id, embed_fn=_identity_embedding)

    status = book_status(vault)
    assert status["total_chunks"] >= 1
    assert status["books_indexed"] >= 1
    assert status["degraded"] is False


def test_status_empty_vault(tmp_path: Path) -> None:
    vault, _, _, _ = _setup_vault(tmp_path)
    status = book_status(vault)
    assert status["total_chunks"] == 0
    assert status["degraded"] is False


# ── Search ──────────────────────────────────────────────────────────────


def test_search_returns_cited_results(tmp_path: Path) -> None:
    vault, source_id, src_path, _ = _setup_vault(tmp_path)
    ingest_book(vault, src_path, source_id=source_id, embed_fn=_identity_embedding)

    results = search_books(vault, "beginnings", n_results=3, embed_fn=_identity_embedding)
    assert len(results) >= 1
    r = results[0]
    assert r["source_id"] == source_id
    assert "chunk_hash" in r
    assert r["score"] is not None


def test_search_empty_vault(tmp_path: Path) -> None:
    vault, _, _, _ = _setup_vault(tmp_path)
    results = search_books(vault, "anything", embed_fn=_identity_embedding)
    assert results == []


# ── Delete ──────────────────────────────────────────────────────────────


def test_delete_removes_source_chunks(tmp_path: Path) -> None:
    vault, source_id, src_path, _ = _setup_vault(tmp_path)
    ingest_book(vault, src_path, source_id=source_id, embed_fn=_identity_embedding)

    before = book_status(vault)["total_chunks"]
    result = delete_book(vault, source_id)
    assert result["chunks_removed"] == before
    assert book_status(vault)["total_chunks"] == 0


def test_delete_nonexistent_source_is_noop(tmp_path: Path) -> None:
    vault, _, _, _ = _setup_vault(tmp_path)
    result = delete_book(vault, "nonexistent")
    assert result["chunks_removed"] == 0


# ── Rebuild ─────────────────────────────────────────────────────────────


def test_rebuild_is_idempotent(tmp_path: Path) -> None:
    vault, source_id, src_path, _ = _setup_vault(tmp_path)

    r1 = rebuild_books(vault, src_path, source_id=source_id, embed_fn=_identity_embedding)
    r2 = rebuild_books(vault, src_path, source_id=source_id, embed_fn=_identity_embedding)

    assert r1["chunk_count"] == r2["chunk_count"]


# ── Embedding ───────────────────────────────────────────────────────────


def test_identity_embedding_is_deterministic() -> None:
    v1 = _identity_embedding(["hello"])
    v2 = _identity_embedding(["hello"])
    assert v1 == v2


def test_identity_embedding_differentiates_texts() -> None:
    v1 = _identity_embedding(["alpha"])
    v2 = _identity_embedding(["beta"])
    assert v1 != v2
