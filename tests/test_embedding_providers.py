"""Tests for local embedding providers and provider resolution."""

import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest
import yaml

from constellation.embedding_providers import (
    EmbeddingProviderError,
    local_hashing_embedding,
    resolve_embedding_provider,
)
from constellation.frontmatter import render_frontmatter
from constellation.models import (
    Claim,
    EntityKind,
    EntityRecord,
    Sensitivity,
    SourceItem,
    generate_ulid,
)
from constellation.semantic_index import (
    build_from_vault,
    semantic_index_status,
    semantic_search,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _add_entity(vault, title: str, sensitivity: Sensitivity) -> str:
    entity = EntityRecord(
        id=generate_ulid(),
        type=EntityKind.COMPANY,
        title=title,
        status="active",
        sensitivity=sensitivity,
        source_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "entities" / f"{entity.id}.md").write_text(
        render_frontmatter(entity.model_dump(mode="json", exclude_none=True), f"# {title}\n"),
        encoding="utf-8",
    )
    return entity.id


def _add_claim(vault, subject_id: str, source_id: str, text: str) -> str:
    claim = Claim(
        id=generate_ulid(),
        title=text[:60],
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        subject_id=subject_id,
        predicate="describes",
        object_literal=text,
        source_ids=[source_id],
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "claims" / f"{claim.id}.md").write_text(
        render_frontmatter(claim.model_dump(mode="json", exclude_none=True), "# Claim\n"),
        encoding="utf-8",
    )
    return claim.id


def _add_source(vault) -> str:
    source = SourceItem(
        id=generate_ulid(),
        type="source_item",
        title="Evidence source",
        status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_hash=hashlib.sha256(b"evidence").hexdigest(),
        original_path="Library/Files/evidence.txt",
        media_type="text/plain",
        created_at=NOW,
        updated_at=NOW,
    )
    (vault / "source-items" / f"{source.id}.md").write_text(
        render_frontmatter(source.model_dump(mode="json", exclude_none=True), "# Source\n"),
        encoding="utf-8",
    )
    return source.id


def test_local_hashing_embedding_is_deterministic() -> None:
    first = local_hashing_embedding(["field intelligence analysis"])
    second = local_hashing_embedding(["field intelligence analysis"])
    assert first == second


def test_local_hashing_embedding_similar_texts_score_higher() -> None:
    from constellation.semantic_index import _cosine

    vecs = local_hashing_embedding(
        [
            "apple supply chain chip orders",
            "apple supply chain chip suppliers",
            "quantum gravity lecture notes",
        ]
    )
    similar = _cosine(vecs[0], vecs[1])
    dissimilar = _cosine(vecs[0], vecs[2])
    assert similar > dissimilar
    assert similar > 0.0


def test_resolve_explicit_local_hashing(tmp_path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    provider = resolve_embedding_provider(vault, name="local-hashing")
    assert provider is local_hashing_embedding


def test_resolve_unknown_provider_fails_closed(tmp_path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    with pytest.raises(EmbeddingProviderError, match="unknown embedding provider"):
        resolve_embedding_provider(vault, name="made-up-provider")


def test_resolve_from_vault_config(tmp_path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    config_path = vault / ".constellation/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["semantic"] = {"embedding_provider": "local-hashing"}
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    provider = resolve_embedding_provider(vault)
    assert provider is local_hashing_embedding


def test_resolve_unconfigured_fails_closed(tmp_path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    with pytest.raises(EmbeddingProviderError, match="no embedding provider configured"):
        resolve_embedding_provider(vault)


def test_build_from_vault_indexes_canonical_records(tmp_path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    source_id = _add_source(vault)
    public_entity = _add_entity(vault, "Public Orchard Supply", Sensitivity.PUBLIC)
    internal_entity = _add_entity(vault, "Internal Chip Logistics", Sensitivity.INTERNAL)
    claim_id = _add_claim(vault, internal_entity, source_id, "chip supply chain orders")

    result = build_from_vault(vault, provider=local_hashing_embedding)

    assert result["status"] == "built"
    assert result["total_entries"] == 4  # 1 source + 2 entities + 1 claim
    assert result["skipped_invalid"] == 0

    status = semantic_index_status(vault)
    assert status["status"] == "ready"
    assert status["stale"] is False
    assert status["provider"] == "local-hashing"

    public_hits = semantic_search(
        vault, "orchard supply", sensitivity_ceiling="public", embed_fn=local_hashing_embedding
    )
    assert {hit["id"] for hit in public_hits} == {public_entity}

    widened = semantic_search(
        vault, "chip supply", sensitivity_ceiling="internal", embed_fn=local_hashing_embedding
    )
    assert internal_entity in {hit["id"] for hit in widened} or claim_id in {hit["id"] for hit in widened}


def test_build_from_vault_reports_invalid_records_visibly(tmp_path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _add_entity(vault, "Valid Entity", Sensitivity.INTERNAL)
    (vault / "entities" / "broken.md").write_text("---\nnot: valid\n", encoding="utf-8")

    result = build_from_vault(vault, provider=local_hashing_embedding)

    assert result["skipped_invalid"] == 1
    assert any("broken.md" in str(item) for item in result["invalid_records"])


def test_semantic_status_reports_stale_after_canonical_change(tmp_path) -> None:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    _add_entity(vault, "Original Entity", Sensitivity.INTERNAL)
    build_from_vault(vault, provider=local_hashing_embedding)
    assert semantic_index_status(vault)["stale"] is False

    _add_entity(vault, "New Entity After Build", Sensitivity.INTERNAL)

    status = semantic_index_status(vault)
    assert status["stale"] is True

    build_from_vault(vault, provider=local_hashing_embedding)
    assert semantic_index_status(vault)["stale"] is False


def test_hybrid_search_marks_stale_index_degraded_but_serves(tmp_path) -> None:
    from constellation.hybrid_retrieval import hybrid_search

    vault = tmp_path / "vault"
    initialize_vault(vault)
    _add_entity(vault, "Orchard Supply Chain", Sensitivity.PUBLIC)
    build_from_vault(vault, provider=local_hashing_embedding)

    fresh = hybrid_search(
        vault, "orchard supply", sensitivity_ceiling="public", embed_fn=local_hashing_embedding
    )
    assert fresh["degraded"] is False

    _add_entity(vault, "Unrelated Later Entity", Sensitivity.PUBLIC)
    stale = hybrid_search(
        vault, "orchard supply", sensitivity_ceiling="public", embed_fn=local_hashing_embedding
    )
    assert stale["degraded"] is True
    assert stale["stale"] is True
    assert stale["results"], "stale index still serves results with explicit degradation"


def test_semantic_cli_build_status_delete_with_configured_provider(tmp_path) -> None:
    from constellation.cli import build_parser, run_action

    vault = tmp_path / "vault"
    initialize_vault(vault)
    _add_entity(vault, "CLI Indexed Entity", Sensitivity.INTERNAL)
    config_path = vault / ".constellation/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["semantic"] = {"embedding_provider": "local-hashing"}
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    def invoke(*args: str) -> Any:
        values = vars(build_parser().parse_args(list(args)))
        return run_action(str(values.pop("command")), values)

    built = invoke("semantic", "build", str(vault))
    assert built["status"] == "built"
    assert built["total_entries"] == 1

    status = invoke("semantic", "status", str(vault))
    assert status["status"] == "ready"
    assert status["provider"] == "local-hashing"
    assert status["stale"] is False

    hybrid = invoke("hybrid", str(vault), "CLI Indexed", "--sensitivity", "internal")
    assert hybrid["degraded"] is False
    assert hybrid["semantic_count"] >= 1

    deleted = invoke("semantic", "delete", str(vault))
    assert deleted["status"] == "deleted"
    assert invoke("semantic", "status", str(vault))["status"] == "missing"
