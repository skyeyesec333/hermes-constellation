"""Book intelligence — semantic search over ingested books.

Phase 13d: chunk → embed → index → search. Uses ChromaDB (SQLite-backed,
no external services) with sentence-transformers for embeddings.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .vault import is_initialized


class BookIntelligenceError(RuntimeError):
    """Raised when book intelligence operations fail."""


def _get_collection_name(vault: Path) -> str:
    """Stable collection name derived from vault path."""
    vault_hash = hashlib.sha256(str(vault.absolute()).encode()).hexdigest()[:12]
    return f"constellation-books-{vault_hash}"


def _get_or_create_collection(vault: Path):
    """Get or create the ChromaDB collection for this vault."""
    import chromadb
    persist_dir = str(vault / ".constellation" / "chromadb")
    client = chromadb.PersistentClient(path=persist_dir)
    name = _get_collection_name(vault)
    return client.get_or_create_collection(name=name)


def _chunk_text(text: str, title: str, max_chars: int = 2000) -> list[dict[str, object]]:
    """Split text into overlapping chunks at paragraph boundaries.

    Returns list of dicts with chunk_id, content, title, chunk_index.
    """
    # Split by double newlines (paragraphs), then by single newlines if too large
    paragraphs = text.split("\n\n")
    chunks: list[dict[str, object]] = []
    current: list[str] = []
    current_len = 0
    chunk_idx = 0

    def _flush():
        nonlocal chunk_idx, current, current_len
        if current:
            content = "\n\n".join(current).strip()
            if content:
                chunks.append({
                    "chunk_id": f"{title}-{chunk_idx:04d}",
                    "content": content[:max_chars],
                    "title": title,
                    "chunk_index": chunk_idx,
                })
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


def _embedding_function():
    """Return a local sentence-transformers embedding function."""
    try:
        from chromadb.utils import embedding_functions
        return embedding_functions.DefaultEmbeddingFunction()
    except Exception:
        raise BookIntelligenceError(
            "ChromaDB embedding function unavailable. Install sentence-transformers: "
            "pip install sentence-transformers"
        )


def ingest_book(vault: Path | str, source_path: Path | str, *, title: str | None = None) -> dict[str, object]:
    """Ingest a book (preserved source) into the ChromaDB collection.

    Reads the preserved source markdown, chunks it, embeds and indexes.
    Returns stats dict with chunk_count and collection_name.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise BookIntelligenceError("vault is not initialized")

    src = Path(source_path)
    if not src.is_file():
        raise BookIntelligenceError(f"source file not found: {src}")

    text = src.read_text(encoding="utf-8")
    if not text.strip():
        raise BookIntelligenceError("source file is empty")

    book_title = title or src.stem
    chunks = _chunk_text(text, book_title)

    if not chunks:
        raise BookIntelligenceError("no extractable chunks from source")

    collection = _get_or_create_collection(vault)

    ids = [c["chunk_id"] for c in chunks]
    documents = [c["content"] for c in chunks]
    metadatas = [{"title": str(c["title"]), "chunk_index": int(c["chunk_index"])} for c in chunks]

    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )

    return {
        "status": "indexed",
        "collection": _get_collection_name(vault),
        "chunk_count": len(chunks),
        "title": book_title,
    }


def search_books(
    vault: Path | str,
    query: str,
    *,
    n_results: int = 5,
) -> list[dict[str, object]]:
    """Semantic search across all indexed books.

    Returns list of dicts with chunk_id, content, title, score.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise BookIntelligenceError("vault is not initialized")

    collection = _get_or_create_collection(vault)
    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[query],
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

        output.append({
            "chunk_id": chunk_id,
            "content": content[:2000],
            "title": str(metadata.get("title", "")) if isinstance(metadata, dict) else "",
            "score": round(float(1.0 - float(distance)), 4) if distance else 0.0,
        })

    return output


def book_stats(vault: Path | str) -> dict[str, object]:
    """Return stats about the book collection."""
    vault = Path(vault).absolute()
    try:
        collection = _get_or_create_collection(vault)
        count = collection.count()
    except Exception:
        count = 0

    return {
        "collection": _get_collection_name(vault),
        "total_chunks": count,
        "books_indexed": len(set(
            str(m.get("title", ""))
            for m in (collection.get()["metadatas"] if count > 0 else [])
        )) if count > 0 else 0,
    }
