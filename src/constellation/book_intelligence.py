"""Book intelligence — semantic search over ingested books.

Chunks, embeds, indexes with canonical source identity and anchors.
Uses ChromaDB (SQLite-backed, no external services) with sentence-transformers
for embeddings. Real backend is optional; tests inject a fake.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .frontmatter import parse_frontmatter
from .models import SourceItem
from .storage import safe_relative_path
from .vault import is_initialized


class BookIntelligenceError(RuntimeError):
    """Raised when book intelligence operations fail."""


# ── Embedding provider interface ────────────────────────────────────────


class EmbeddingProvider(Protocol):
    def __call__(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class _BookChunk:
    chunk_id: str
    content: str
    source_id: str
    source_hash: str
    chunk_index: int
    chunk_hash: str


# ── Collection helpers ──────────────────────────────────────────────────


def _get_collection_name(vault: Path) -> str:
    vault_hash = hashlib.sha256(str(vault.absolute()).encode()).hexdigest()[:12]
    return f"constellation-books-{vault_hash}"


def _get_or_create_collection(vault: Path):
    import chromadb

    persist_dir = str(vault / ".constellation" / "chromadb")
    client = chromadb.PersistentClient(path=persist_dir)
    name = _get_collection_name(vault)
    return client.get_or_create_collection(name=name)


# ── Chunking ────────────────────────────────────────────────────────────


def _chunk_text(
    text: str,
    *,
    source_id: str,
    source_hash: str,
    max_chars: int = 2000,
) -> list[_BookChunk]:
    """Split text into overlapping chunks at paragraph boundaries.

    Every chunk gets a deterministic ID derived from source_hash + anchor.
    """
    paragraphs = text.split("\n\n")
    chunks: list[_BookChunk] = []
    current: list[str] = []
    current_len = 0
    chunk_idx = 0

    def _flush() -> None:
        nonlocal chunk_idx, current, current_len
        if current:
            content = "\n\n".join(current).strip()
            if content:
                chunk_hash = hashlib.sha256(content.encode()).hexdigest()
                chunk_id = f"{source_hash[:16]}-{chunk_idx:06d}"
                chunks.append(
                    _BookChunk(
                        chunk_id=chunk_id,
                        content=content[:max_chars],
                        source_id=source_id,
                        source_hash=source_hash,
                        chunk_index=chunk_idx,
                        chunk_hash=chunk_hash,
                    )
                )
                chunk_idx += 1
            current = []
            current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > max_chars:
            _flush()
        current.append(para)
        current_len += len(para)

    _flush()
    return chunks


# ── Embedding ────────────────────────────────────────────────────────────


def _default_embedding_function() -> EmbeddingProvider:
    """Return the real sentence-transformers embedding function."""
    try:
        from chromadb.utils import embedding_functions

        fn = embedding_functions.DefaultEmbeddingFunction()

        def _embed(texts: list[str]) -> list[list[float]]:
            return fn(texts)

        return _embed
    except Exception:
        raise BookIntelligenceError(
            "ChromaDB embedding function unavailable. Install sentence-transformers: "
            "pip install sentence-transformers"
        )


def _identity_embedding(texts: list[str]) -> list[list[float]]:
    """Fake embedding: returns a deterministic vector from text hash. For tests only."""
    vectors: list[list[float]] = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        v = [(b / 255.0) for b in h[:128]]  # 128-dim from first 128 bytes
        while len(v) < 128:
            v.append(0.0)
        vectors.append(v)
    return vectors


# ── Canonical source resolution ─────────────────────────────────────────


def _require_canonical_source(vault: Path, source_id: str) -> SourceItem:
    path = safe_relative_path(vault, Path("source-items") / f"{source_id}.md")
    if not path.is_file() or path.is_symlink():
        raise BookIntelligenceError(f"canonical source item not found: {source_id}")
    try:
        metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        record = SourceItem.model_validate(metadata, strict=False)
    except Exception as exc:
        raise BookIntelligenceError(f"canonical source item is invalid: {source_id}") from exc
    if record.id != source_id:
        raise BookIntelligenceError(f"source item ID mismatch: {source_id}")
    return record


# ── Public API ───────────────────────────────────────────────────────────


def ingest_book(
    vault: Path | str,
    source_path: Path | str,
    *,
    source_id: str,
    title: str | None = None,
    embed_fn: EmbeddingProvider | None = None,
) -> dict[str, object]:
    """Index a preserved book source into the ChromaDB collection.

    Requires a canonical SourceItem with matching source_id.
    Chunk IDs are deterministic (source_hash prefix + index).
    Metadata includes source ID, hash, path, sensitivity, chunk hash.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise BookIntelligenceError("vault is not initialized")

    source_item = _require_canonical_source(vault, source_id)

    src = Path(source_path)
    if not src.is_file():
        raise BookIntelligenceError(f"source file not found: {src}")

    text = src.read_text(encoding="utf-8")
    if not text.strip():
        raise BookIntelligenceError("source file is empty")

    computed_hash = hashlib.sha256(text.encode()).hexdigest()
    if computed_hash != source_item.source_hash:
        raise BookIntelligenceError(
            f"source content hash does not match canonical source hash: {source_id}"
        )

    book_title = title or src.stem
    chunks = _chunk_text(text, source_id=source_id, source_hash=source_item.source_hash)

    if not chunks:
        raise BookIntelligenceError("no extractable chunks from source")

    provider = embed_fn or _default_embedding_function()
    collection = _get_or_create_collection(vault)

    ids = [c.chunk_id for c in chunks]
    documents = [c.content for c in chunks]
    metadatas = [
        {
            "source_id": c.source_id,
            "source_hash": c.source_hash,
            "title": book_title,
            "chunk_index": c.chunk_index,
            "chunk_hash": c.chunk_hash,
            "sensitivity": source_item.sensitivity.value,
            "embedding_version": "1",
        }
        for c in chunks
    ]
    embeddings = provider(documents)

    collection.add(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

    return {
        "status": "indexed",
        "collection": _get_collection_name(vault),
        "source_id": source_id,
        "chunk_count": len(chunks),
        "title": book_title,
    }


def search_books(
    vault: Path | str,
    query: str,
    *,
    n_results: int = 5,
    embed_fn: EmbeddingProvider | None = None,
) -> list[dict[str, object]]:
    """Semantic search across all indexed books.

    Returns list of dicts with chunk_id, content, title, score, source_id,
    source_hash, chunk_hash.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise BookIntelligenceError("vault is not initialized")

    collection = _get_or_create_collection(vault)
    if collection.count() == 0:
        return []

    provider = embed_fn or _default_embedding_function()
    query_embedding = provider([query])

    results = collection.query(
        query_embeddings=query_embedding,
        n_results=min(n_results, collection.count()),
    )

    if not results or not results.get("ids") or not results["ids"][0]:
        return []

    output: list[dict[str, object]] = []
    for i in range(len(results["ids"][0])):
        chunk_id = results["ids"][0][i]
        content = results["documents"][0][i] if results.get("documents") else ""
        metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
        distance = results["distances"][0][i] if results.get("distances") else 0.0

        md = metadata if isinstance(metadata, dict) else {}
        output.append(
            {
                "chunk_id": chunk_id,
                "content": str(content)[:2000],
                "title": str(md.get("title", "")),
                "source_id": str(md.get("source_id", "")),
                "source_hash": str(md.get("source_hash", "")),
                "chunk_hash": str(md.get("chunk_hash", "")),
                "score": round(float(1.0 - float(distance)), 4) if distance else 0.0,
            }
        )

    return output


def book_status(vault: Path | str) -> dict[str, object]:
    """Return stats about the book collection.

    Never swallows backend errors — reports degradation explicitly.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise BookIntelligenceError("vault is not initialized")

    try:
        collection = _get_or_create_collection(vault)
        count = collection.count()
    except Exception as exc:
        return {
            "collection": _get_collection_name(vault),
            "total_chunks": 0,
            "books_indexed": 0,
            "degraded": True,
            "error": f"ChromaDB backend error: {exc}",
        }

    if count == 0:
        return {
            "collection": _get_collection_name(vault),
            "total_chunks": 0,
            "books_indexed": 0,
            "degraded": False,
        }

    try:
        raw = collection.get()
        metadatas = raw.get("metadatas", [])
        unique_titles = len({str(m.get("title", "")) for m in metadatas if isinstance(m, dict)})
        unique_sources = len({str(m.get("source_id", "")) for m in metadatas if isinstance(m, dict)})
    except Exception as exc:
        return {
            "collection": _get_collection_name(vault),
            "total_chunks": count,
            "books_indexed": 0,
            "degraded": True,
            "error": f"metadata query failed: {exc}",
        }

    return {
        "collection": _get_collection_name(vault),
        "total_chunks": count,
        "books_indexed": unique_titles,
        "unique_sources": unique_sources,
        "degraded": False,
    }


def delete_book(vault: Path | str, source_id: str) -> dict[str, object]:
    """Delete all chunks belonging to a source from the collection."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise BookIntelligenceError("vault is not initialized")

    collection = _get_or_create_collection(vault)
    if collection.count() == 0:
        return {"status": "deleted", "source_id": source_id, "chunks_removed": 0}

    try:
        raw = collection.get()
        all_ids = raw.get("ids", [])
        all_metadatas = raw.get("metadatas", [])

        to_delete: list[str] = []
        for i, chunk_id in enumerate(all_ids):
            md = all_metadatas[i] if i < len(all_metadatas) else {}
            if isinstance(md, dict) and md.get("source_id") == source_id:
                to_delete.append(chunk_id)

        if to_delete:
            collection.delete(ids=to_delete)

        return {"status": "deleted", "source_id": source_id, "chunks_removed": len(to_delete)}
    except Exception as exc:
        raise BookIntelligenceError(f"delete failed: {exc}") from exc


def rebuild_books(
    vault: Path | str,
    source_path: Path | str,
    *,
    source_id: str,
    title: str | None = None,
    embed_fn: EmbeddingProvider | None = None,
) -> dict[str, object]:
    """Delete and re-ingest a book. Idempotent — same source always produces same chunks."""
    delete_book(vault, source_id)
    return ingest_book(vault, source_path, source_id=source_id, title=title, embed_fn=embed_fn)
