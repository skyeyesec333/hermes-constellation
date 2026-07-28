"""Hybrid retrieval — fuses lexical (FTS5) and semantic rankings.

Reciprocal-rank fusion produces a single ranked list with both scores.
FTS5 remains authoritative; semantic failure degrades gracefully to FTS-only
with degraded=true — never silently looks like complete hybrid success.
"""

from __future__ import annotations

from pathlib import Path

from .retrieval import search as lexical_search
from .semantic_index import EmbeddingProvider, semantic_search
from .vault import is_initialized


class HybridRetrievalError(RuntimeError):
    """Raised when hybrid retrieval fails."""


def _rrf_score(rank: int, k: int = 60) -> float:
    """Reciprocal rank fusion score."""
    return 1.0 / (k + rank + 1)


def hybrid_search(
    vault: Path | str,
    query: str,
    *,
    n_results: int = 10,
    sensitivity_ceiling: str = "internal",
    embed_fn: EmbeddingProvider | None = None,
) -> dict[str, object]:
    """Run fused lexical + semantic search.

    Returns dict with results list, degraded flag, fusion metadata.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise HybridRetrievalError("vault is not initialized")

    # Lexical (authoritative)
    lexical_results = lexical_search(
        vault, query, limit=max(n_results * 2, 20), sensitivity_ceiling=sensitivity_ceiling
    )

    # Semantic (degradable)
    semantic_results: list[dict] = []
    degraded = False
    stale = False
    try:
        from .semantic_index import semantic_index_status

        idx_status = semantic_index_status(vault)
        if idx_status.get("status") != "ready":
            degraded = True
        else:
            stale = bool(idx_status.get("stale"))
            semantic_results = semantic_search(
                vault,
                query,
                n_results=n_results * 2,
                sensitivity_ceiling=sensitivity_ceiling,
                embed_fn=embed_fn,
            )
            if stale:
                degraded = True
    except Exception:
        degraded = True

    # Build RRF scores
    rrf: dict[str, dict] = {}

    for rank, item in enumerate(lexical_results):
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("path", item.get("id", "")))
        if not doc_id:
            continue
        rrf.setdefault(doc_id, {"lexical_rank": rank, "lexical_score": item.get("score", 0), "lexical_item": item})
        rrf[doc_id]["lexical_rrf"] = _rrf_score(rank)

    for rank, item in enumerate(semantic_results):
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("path", item.get("id", "")))
        if not doc_id:
            continue
        entry = rrf.setdefault(doc_id, {})
        entry["semantic_rank"] = rank
        entry["semantic_score"] = item.get("semantic_score", 0)
        entry["semantic_rrf"] = _rrf_score(rank)
        entry["semantic_item"] = item

    # Compute fused scores
    fused: list[tuple[float, dict]] = []
    for doc_id, entry in rrf.items():
        lex_rrf = entry.get("lexical_rrf", 0)
        sem_rrf = entry.get("semantic_rrf", 0)
        fused_score = lex_rrf + sem_rrf
        fused.append((fused_score, doc_id, entry))

    fused.sort(key=lambda x: x[0], reverse=True)

    results: list[dict[str, object]] = []
    for fused_score, doc_id, entry in fused[:n_results]:
        result: dict[str, object] = {
            "path": doc_id,
            "fused_rank_score": round(fused_score, 4),
            "lexical_score": entry.get("lexical_score", 0),
            "semantic_score": entry.get("semantic_score"),
            "lexical_rank": entry.get("lexical_rank"),
            "semantic_rank": entry.get("semantic_rank"),
        }
        # Include canonical fields from lexical or semantic item
        for key in ("id", "title", "snippet", "source_hash", "sensitivity"):
            lex_item = entry.get("lexical_item")
            if isinstance(lex_item, dict) and key in lex_item:
                result[key] = lex_item[key]
            elif "semantic_item" in entry and isinstance(entry["semantic_item"], dict):
                sem_item = entry["semantic_item"]
                if key in sem_item:
                    result[key] = sem_item[key]
        results.append(result)

    return {
        "results": results,
        "total_fused": len(results),
        "degraded": degraded,
        "stale": stale,
        "fusion_method": "reciprocal_rank_fusion",
        "lexical_count": len(lexical_results),
        "semantic_count": len(semantic_results),
    }
