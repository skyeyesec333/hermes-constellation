"""Disposable semantic side-index for hybrid retrieval.

FTS5 remains authoritative; this index is a local disposable cache.
Provider-neutral embedding interface, separate collections by sensitivity.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from .vault import is_initialized

_SEMANTIC_DIR = Path(".constellation/semantic-index")


class SemanticIndexError(RuntimeError):
    """Raised when semantic index operations fail."""


class EmbeddingProvider(Protocol):
    def __call__(self, texts: list[str]) -> list[list[float]]: ...


def _identity_embedding(texts: list[str]) -> list[list[float]]:
    """Deterministic fake embedding from text hash. For tests."""
    vectors: list[list[float]] = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        v = [(b / 255.0) for b in h[:128]]
        while len(v) < 128:
            v.append(0.0)
        vectors.append(v)
    return vectors


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _index_path(vault: Path, generation_id: str) -> Path:
    return vault / _SEMANTIC_DIR / f"index-{generation_id}.json"


def _state_path(vault: Path) -> Path:
    return vault / _SEMANTIC_DIR / "state.json"


def _read_state(vault: Path) -> dict:
    path = _state_path(vault)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(vault: Path, state: dict) -> None:
    path = _state_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_semantic_index(
    vault: Path | str,
    records: list[dict[str, object]],
    *,
    embed_fn: EmbeddingProvider | None = None,
) -> dict[str, object]:
    """Build a disposable semantic index from records.

    Each record must have: id, text (to embed), path, source_hash, sensitivity.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise SemanticIndexError("vault is not initialized")

    if embed_fn is None:
        raise SemanticIndexError(
            "production semantic build requires an explicitly configured embedding provider"
        )
    provider = embed_fn
    generation_id = hashlib.sha256(str(hash(tuple(r.get("id", "") for r in records))).encode()).hexdigest()[:16]

    # Separate by sensitivity
    by_sensitivity: dict[str, list[dict]] = {}
    for r in records:
        sens = str(r.get("sensitivity", "internal"))
        by_sensitivity.setdefault(sens, []).append(r)

    entries: list[dict] = []
    for sens, recs in by_sensitivity.items():
        texts = [str(r.get("text", "")) for r in recs]
        embeddings = provider(texts)
        for i, r in enumerate(recs):
            entries.append({
                "id": str(r.get("id", "")),
                "path": str(r.get("path", "")),
                "source_hash": str(r.get("source_hash", "")),
                "sensitivity": sens,
                "embedding": embeddings[i],
                "chunk_index": i,
            })

    index_path = _index_path(vault, generation_id)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    state = {
        "generation_id": generation_id,
        "total_entries": len(entries),
        "sensitivities": list(by_sensitivity.keys()),
        "built_at": str(Path(index_path).stat().st_mtime if index_path.exists() else 0),
    }
    _write_state(vault, state)

    return {"status": "built", "generation_id": generation_id, "total_entries": len(entries)}


def semantic_search(
    vault: Path | str,
    query: str,
    *,
    n_results: int = 10,
    sensitivity_ceiling: str = "internal",
    embed_fn: EmbeddingProvider | None = None,
) -> list[dict[str, object]]:
    """Search the semantic index and return scored results."""
    vault = Path(vault).absolute()
    if embed_fn is None:
        raise SemanticIndexError(
            "production semantic search requires an explicitly configured embedding provider"
        )
    state = _read_state(vault)
    gen_id = state.get("generation_id")
    if not gen_id:
        return []

    index_path = _index_path(vault, str(gen_id))
    if not index_path.is_file():
        return []

    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    provider = embed_fn
    query_vec = provider([query])[0]

    sensitivity_order = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
    ceiling = sensitivity_order.get(sensitivity_ceiling, 1)

    scored: list[tuple[float, dict]] = []
    for entry in entries:
        sens = str(entry.get("sensitivity", "internal"))
        if sensitivity_order.get(sens, 0) > ceiling:
            continue
        emb = entry.get("embedding")
        if not isinstance(emb, list):
            continue
        score = _cosine(query_vec, emb)
        scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    results: list[dict[str, object]] = []
    for score, entry in scored[:n_results]:
        results.append({
            "id": entry.get("id", ""),
            "path": entry.get("path", ""),
            "source_hash": entry.get("source_hash", ""),
            "semantic_score": round(score, 4),
            "sensitivity": entry.get("sensitivity", ""),
        })
    return results


def semantic_index_status(vault: Path | str) -> dict[str, object]:
    """Return semantic index state, staleness, degradation."""
    vault = Path(vault).absolute()
    state = _read_state(vault)
    gen_id = state.get("generation_id")
    if not gen_id:
        return {"status": "missing", "degraded": False}

    index_path = _index_path(vault, str(gen_id))
    if not index_path.is_file():
        return {"status": "missing", "generation_id": gen_id, "degraded": True}

    return {
        "status": "ready",
        "generation_id": gen_id,
        "total_entries": state.get("total_entries", 0),
        "degraded": False,
    }


def delete_semantic_index(vault: Path | str) -> dict[str, object]:
    """Delete the semantic index and state."""
    vault = Path(vault).absolute()
    state = _read_state(vault)
    gen_id = state.get("generation_id")
    if gen_id:
        idx = _index_path(vault, str(gen_id))
        if idx.is_file():
            idx.unlink()
    sp = _state_path(vault)
    if sp.is_file():
        sp.unlink()
    return {"status": "deleted"}
