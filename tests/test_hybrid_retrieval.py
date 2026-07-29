"""Tests for hybrid retrieval — semantic index + RRF fusion."""

from pathlib import Path

import pytest

from constellation.hybrid_retrieval import hybrid_search
from constellation.semantic_index import (
    SemanticIndexError,
    _identity_embedding,
    build_semantic_index,
    delete_semantic_index,
    semantic_index_status,
    semantic_search,
)
from constellation.vault import initialize_vault


def _sample_records() -> list[dict[str, object]]:
    return [
        {"id": "r1", "text": "The quick brown fox jumps over the lazy dog", "path": "claims/r1.md", "source_hash": "a" * 64, "sensitivity": "internal"},
        {"id": "r2", "text": "Machine learning transforms data into insights", "path": "claims/r2.md", "source_hash": "b" * 64, "sensitivity": "internal"},
        {"id": "r3", "text": "Constellation is a local-first intelligence workspace", "path": "claims/r3.md", "source_hash": "c" * 64, "sensitivity": "internal"},
        {"id": "r4", "text": "Restricted document with sensitive information", "path": "claims/r4.md", "source_hash": "d" * 64, "sensitivity": "restricted"},
    ]


def test_production_semantic_build_requires_explicit_embedding_provider(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    with pytest.raises(SemanticIndexError, match="embedding provider"):
        build_semantic_index(vault, _sample_records())
    assert semantic_index_status(vault)["status"] == "missing"


def test_build_and_search_semantic_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    result = build_semantic_index(vault, _sample_records(), embed_fn=_identity_embedding)
    assert result["status"] == "built"
    assert result["total_entries"] == 4

    status = semantic_index_status(vault)
    assert status["status"] == "ready"


def test_semantic_search_returns_results(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    build_semantic_index(vault, _sample_records(), embed_fn=_identity_embedding)
    results = semantic_search(vault, "fox jumps", n_results=3, embed_fn=_identity_embedding)
    assert len(results) >= 1
    assert "semantic_score" in results[0]


def test_no_provider_semantic_search_rejects_before_missing_index_lookup(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    with pytest.raises(SemanticIndexError, match="embedding provider"):
        semantic_search(vault, "anything")


def test_production_semantic_search_requires_explicit_embedding_provider(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    build_semantic_index(vault, _sample_records(), embed_fn=_identity_embedding)

    with pytest.raises(SemanticIndexError, match="embedding provider"):
        semantic_search(vault, "fox jumps")


def test_semantic_search_respects_sensitivity(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    build_semantic_index(vault, _sample_records(), embed_fn=_identity_embedding)
    results = semantic_search(
        vault,
        "restricted",
        sensitivity_ceiling="internal",
        n_results=10,
        embed_fn=_identity_embedding,
    )
    # r4 is "restricted" — should be excluded when ceiling is "internal"
    paths = [r.get("path") for r in results]
    assert "claims/r4.md" not in paths


def test_delete_semantic_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    build_semantic_index(vault, _sample_records(), embed_fn=_identity_embedding)
    delete_semantic_index(vault)
    assert semantic_index_status(vault)["status"] == "missing"


def test_hybrid_search_returns_fused_results(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    build_semantic_index(vault, _sample_records(), embed_fn=_identity_embedding)
    result = hybrid_search(vault, "intelligence workspace", n_results=5, embed_fn=_identity_embedding)
    assert "results" in result
    assert "degraded" in result
    # Semantic index exists so should not be degraded
    assert result["degraded"] is False


def test_hybrid_search_real_lexical_lane_contributes(tmp_path: Path) -> None:
    """Regression: hybrid must consume the real retrieval.search packet API.

    The lexical lane silently died when retrieval.search switched to
    evidence-packet dicts (3bcbc15): enumerate() iterated packet keys and
    every entry was dropped, leaving hybrid semantic-only. The mocked test
    below locked in the stale list contract, so nothing caught it. This test
    uses the real FTS5 index end-to-end.
    """
    vault = tmp_path / "vault"
    initialize_vault(vault)
    claims = vault / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    from datetime import UTC, datetime

    from constellation.frontmatter import render_frontmatter

    (claims / "fastpass.md").write_text(
        render_frontmatter(
            {
                "schema_version": "0.1",
                "id": "01ARZ3NDEKTSV4RRFFQ69G5FAW",
                "type": "claim",
                "title": "FastPass incentive guidance",
                "status": "active",
                "sensitivity": "internal",
                "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                "updated_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                "subject_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "predicate": "mentions",
                "object_literal": "BOI FastPass incentive program",
                "source_ids": ["01ARZ3NDEKTSV4RRFFQ69G5FAV"],
            },
            "# FastPass incentive guidance\n\nBOI published updated FastPass incentive guidance for Thailand.\n",
        ),
        encoding="utf-8",
    )

    from constellation.retrieval import build_index

    build_index(vault)

    result = hybrid_search(vault, "FastPass", n_results=5, embed_fn=_identity_embedding)

    assert result["lexical_count"] >= 1, "real FTS5 hit must reach the lexical lane"
    lexical_ranks = [r.get("lexical_rank") for r in result["results"]]
    assert any(rank is not None for rank in lexical_ranks), (
        f"no fused result carries a lexical rank: {result['results']}"
    )


def test_hybrid_search_without_provider_degrades_to_cited_fts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    build_semantic_index(vault, _sample_records(), embed_fn=_identity_embedding)
    monkeypatch.setattr(
        "constellation.hybrid_retrieval.lexical_search",
        lambda *_args, **_kwargs: [
            {
                "id": "fts-1",
                "path": "claims/fts-1.md",
                "title": "Cited FTS result",
                "snippet": "Authoritative lexical evidence.",
                "source_hash": "e" * 64,
                "sensitivity": "internal",
                "score": 1.0,
            }
        ],
    )

    result = hybrid_search(vault, "anything")

    assert result["degraded"] is True
    assert result["semantic_count"] == 0
    results = result["results"]
    assert isinstance(results, list)
    assert isinstance(results[0], dict)
    assert results[0]["path"] == "claims/fts-1.md"
    assert results[0]["source_hash"] == "e" * 64


def test_hybrid_search_degraded_when_no_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    result = hybrid_search(vault, "anything")
    assert result["degraded"] is True
    assert "fusion_method" in result
