"""Tests for Stage 7.4 crystallization.

Contract: a session/work artifact is distilled DETERMINISTICALLY into a
structured digest, preserved with its own hash, and staged as a review-only
candidate_patch targeting source-items/ — the normal gate, nothing
auto-promotes. The digest cites its provenance (source artifact path +
sha256 + actor) end to end.
"""

from pathlib import Path

import pytest

from constellation.crystallize import CrystallizeError, crystallize_artifact
from constellation.review import list_candidates, promote_candidate
from constellation.vault import initialize_vault

ARTIFACT = """# Session notes 2026-07-29

## Decisions

- Adopt review-gated lifecycle for all claim mutations
- Keep public docs claims-only-when-built

## Open questions

- Whether the hotspot uplink can carry the ONNX model download

## Noise

- ok
"""


def _vault_with_artifact(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    artifact = vault / "maintenance" / "session-2026-07-29.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(ARTIFACT, encoding="utf-8")
    return vault, artifact


def test_stages_review_only_candidate_citing_provenance(tmp_path: Path) -> None:
    vault, artifact = _vault_with_artifact(tmp_path)

    result = crystallize_artifact(vault, artifact, actor="cso")

    assert result["status"] == "staged"
    listed = list_candidates(vault)
    assert len(listed) == 1
    assert listed[0]["kind"] == "candidate_patch"
    assert listed[0]["target_path"].startswith("source-items/")
    assert listed[0]["expected_base_hash"] is None
    assert listed[0]["promotable"] is True
    assert result["candidate_id"] == listed[0]["id"]


def test_digest_preserves_structure_and_provenance(tmp_path: Path) -> None:
    vault, artifact = _vault_with_artifact(tmp_path)

    result = crystallize_artifact(vault, artifact, actor="cso")

    assert result["sections"] == 3
    assert result["items"] == 3  # 'ok' (2 chars) is below the minimum item length
    digest_text = Path(result["digest_path"]).read_text(encoding="utf-8") \
        if Path(result["digest_path"]).is_absolute() else (vault / result["digest_path"]).read_text(encoding="utf-8")
    assert "session-2026-07-29.md" in digest_text
    assert result["artifact_sha256"] in digest_text  # cited end to end
    assert "Adopt review-gated lifecycle" in digest_text
    assert "hotspot uplink" in digest_text


def test_nothing_auto_promotes(tmp_path: Path) -> None:
    vault, artifact = _vault_with_artifact(tmp_path)

    crystallize_artifact(vault, artifact, actor="cso")

    source_items = list((vault / "source-items").glob("*.md"))
    assert source_items == []


def test_candidate_promotes_through_normal_path(tmp_path: Path) -> None:
    vault, artifact = _vault_with_artifact(tmp_path)
    result = crystallize_artifact(vault, artifact, actor="cso")

    promoted = promote_candidate(vault, result["candidate_id"], confirm=True, expected_base_hash=None)

    assert promoted["status"] == "promoted"
    from constellation.validation import validate_vault
    assert validate_vault(vault)["invalid"] == 0


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    vault, artifact = _vault_with_artifact(tmp_path)
    first = crystallize_artifact(vault, artifact, actor="cso")

    second = crystallize_artifact(vault, artifact, actor="cso")

    assert second["status"] == "already_staged"
    assert second["candidate_id"] == first["candidate_id"]
    assert len(list_candidates(vault)) == 1


def test_artifact_must_be_inside_vault(tmp_path: Path) -> None:
    vault, _ = _vault_with_artifact(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# nope\n", encoding="utf-8")

    with pytest.raises(CrystallizeError, match="inside the vault"):
        crystallize_artifact(vault, outside, actor="cso")


def test_missing_artifact_fails_closed(tmp_path: Path) -> None:
    vault, _ = _vault_with_artifact(tmp_path)

    with pytest.raises(CrystallizeError, match="not found|missing"):
        crystallize_artifact(vault, vault / "maintenance" / "ghost.md", actor="cso")


def test_actor_required(tmp_path: Path) -> None:
    vault, artifact = _vault_with_artifact(tmp_path)

    with pytest.raises(CrystallizeError, match="actor"):
        crystallize_artifact(vault, artifact, actor="  ")
