"""Tests for hybrid retrieval — semantic index + RRF fusion."""

from pathlib import Path

from constellation.hybrid_retrieval import hybrid_search
from constellation.semantic_index import (
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


def test_build_and_search_semantic_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    result = build_semantic_index(vault, _sample_records())
    assert result["status"] == "built"
    assert result["total_entries"] == 4

    status = semantic_index_status(vault)
    assert status["status"] == "ready"


def test_semantic_search_returns_results(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    build_semantic_index(vault, _sample_records())
    results = semantic_search(vault, "fox jumps", n_results=3)
    assert len(results) >= 1
    assert "semantic_score" in results[0]


def test_semantic_search_respects_sensitivity(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    build_semantic_index(vault, _sample_records())
    results = semantic_search(vault, "restricted", sensitivity_ceiling="internal", n_results=10)
    # r4 is "restricted" — should be excluded when ceiling is "internal"
    paths = [r.get("path") for r in results]
    assert "claims/r4.md" not in paths


def test_delete_semantic_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    build_semantic_index(vault, _sample_records())
    delete_semantic_index(vault)
    assert semantic_index_status(vault)["status"] == "missing"


def test_hybrid_search_returns_fused_results(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    build_semantic_index(vault, _sample_records())
    result = hybrid_search(vault, "intelligence workspace", n_results=5)
    assert "results" in result
    assert "degraded" in result
    # Semantic index exists so should not be degraded
    assert result["degraded"] is False


def test_hybrid_search_degraded_when_no_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)

    result = hybrid_search(vault, "anything")
    assert result["degraded"] is True
    assert "fusion_method" in result
