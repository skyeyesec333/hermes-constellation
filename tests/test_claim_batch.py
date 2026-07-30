import json
from datetime import UTC, datetime
from pathlib import Path

from constellation.claim_batch import main, stage_claims_from_file
from constellation.frontmatter import render_frontmatter
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 30, tzinfo=UTC)
SOURCE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SUBJECT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    source_text = (
        "Fictional values include $43B, $2B, $CART, `quoted code`, "
        "O'Reilly, and Unicode punctuation — “exactly”.\n"
    )
    source = render_frontmatter(
        {
            "schema_version": "0.1",
            "id": SOURCE_ID,
            "type": "source-item",
            "title": "Fictional batch source",
            "status": "active",
            "sensitivity": "internal",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "source_hash": "1" * 64,
            "original_path": "Inbox/Files/batch.txt",
            "media_type": "text/plain",
        },
        source_text,
    )
    (vault / "source-items" / f"{SOURCE_ID}.md").write_text(source, encoding="utf-8")
    return vault


def _claim(object_literal: str, excerpt: str) -> dict[str, object]:
    return {
        "subject_id": SUBJECT_ID,
        "predicate": "mentions_value",
        "object_literal": object_literal,
        "source_ids": [SOURCE_ID],
        "evidence_excerpt": excerpt,
        "claim_status": "inferred",
        "confidence": 0.8,
    }


def test_batch_staging_preserves_shell_metacharacters_and_unicode(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    values = ["$43B", "$2B", "$CART", "`quoted code`", "O'Reilly", "— “exactly”"]
    payload = {
        "claims": [
            _claim(value, value)
            for value in values
        ]
    }
    input_path = tmp_path / "claims.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = stage_claims_from_file(vault, input_path)

    assert result["status"] == "completed"
    assert result["succeeded"] == len(values)
    assert result["failed"] == 0
    candidate_paths = sorted((vault / ".constellation/candidates").glob("claim-*.json"))
    assert len(candidate_paths) == len(values)
    staged_values = {
        json.loads(path.read_text(encoding="utf-8"))["object_literal"]
        for path in candidate_paths
    }
    assert staged_values == set(values)


def test_batch_fails_nonzero_per_item_without_writing_invalid_candidate(
    tmp_path: Path, capsys
) -> None:
    vault = _vault(tmp_path)
    input_path = tmp_path / "claims.json"
    input_path.write_text(
        json.dumps(
            {
                "claims": [
                    _claim("$43B", "$43B"),
                    _claim("bad excerpt", "not present in the source"),
                ]
            }
        ),
        encoding="utf-8",
    )

    exit_code = main([str(vault), str(input_path)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["status"] == "completed_with_failures"
    assert output["succeeded"] == 1
    assert output["failed"] == 1
    assert output["results"][1]["status"] == "failed"
    assert "not verbatim" in output["results"][1]["error"]
    candidates = list((vault / ".constellation/candidates").glob("claim-*.json"))
    assert len(candidates) == 1
