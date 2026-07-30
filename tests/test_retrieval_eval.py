"""Retrieval evaluation harness (Thai retrieval evaluation, TDD).

Known-item-search scoring of lexical FTS5 per tokenizer. The deterministic
core: a no-space Thai title is ONE unicode61 token, so a partial Thai query
(a substring spanning "words") cannot match it — while the trigram
tokenizer recovers it. This quantifies the Thai retrieval gap instead of
asserting it anecdotally."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.models import EntityKind, EntityRecord, Sensitivity, generate_ulid
from constellation.retrieval_eval import (
    EvalCase,
    RetrievalEvalError,
    compare_tokenizers,
    evaluate_lexical,
    first_relevant_rank,
    known_item_cases_from_vault,
    mrr,
    recall_at,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
TRIGram_AVAILABLE = sqlite3.sqlite_version_info >= (3, 34, 0)

THAI_TITLE = "บริษัทเนสท์เลประเทศไทย"  # no-space Thai: one unicode61 token
THAI_PARTIAL_QUERY = "เนสท์เลประเทศไทย"  # substring spanning "words"
ENGLISH_TITLE = "Fictional Weave Dynamics"


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    return vault


def _entity(vault: Path, title: str, body: str) -> EntityRecord:
    record = EntityRecord(
        id=generate_ulid(), type=EntityKind.COMPANY, title=title,
        status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
        created_at=NOW, updated_at=NOW,
    )
    (vault / "entities" / f"{record.id}.md").write_text(
        render_frontmatter(
            record.model_dump(mode="json", exclude_none=True), f"# {title}\n\n{body}\n",
        ),
        encoding="utf-8",
    )
    return record


def _fixture(tmp_path: Path) -> tuple[Path, EntityRecord, EntityRecord]:
    vault = _vault(tmp_path)
    thai = _entity(vault, THAI_TITLE, "ตัวอย่างบันทึกสำหรับการทดสอบการค้นคืน")
    english = _entity(vault, ENGLISH_TITLE, "A fictional company for retrieval tests.")
    return vault, thai, english


# --- metric math -----------------------------------------------------------


def test_metric_math_is_exact() -> None:
    assert first_relevant_rank(["a", "b", "c"], ("b",)) == 1
    assert first_relevant_rank(["a"], ("zzz",)) is None
    assert first_relevant_rank([], ("a",)) is None
    assert recall_at(0, 1) == 1.0
    assert recall_at(4, 5) == 1.0
    assert recall_at(10, 10) == 0.0
    assert recall_at(None, 10) == 0.0
    assert mrr(0) == 1.0
    assert mrr(3) == 0.25
    assert mrr(None) == 0.0


# --- the Thai gap, measured ------------------------------------------------


def test_unicode61_misses_partial_thai_query(tmp_path) -> None:
    vault, thai, _english = _fixture(tmp_path)
    cases = [EvalCase(query=THAI_PARTIAL_QUERY, relevant_ids=(thai.id,), language="thai")]

    result = evaluate_lexical(vault, cases, tokenizer="unicode61")

    assert result["per_case"][0]["rank"] is None
    assert result["recall_at_10"] == 0.0
    assert result["by_language"]["thai"]["recall_at_10"] == 0.0


def test_unicode61_hits_exact_no_space_thai_title(tmp_path) -> None:
    """Exact no-space title IS a single token — unicode61 handles that case."""
    vault, thai, _english = _fixture(tmp_path)
    cases = [EvalCase(query=THAI_TITLE, relevant_ids=(thai.id,), language="thai")]

    result = evaluate_lexical(vault, cases, tokenizer="unicode61")

    assert result["per_case"][0]["rank"] == 0
    assert result["recall_at_1"] == 1.0


def test_trigram_recovers_partial_thai_query(tmp_path) -> None:
    if not TRIGram_AVAILABLE:
        pytest.skip("SQLite < 3.34: trigram tokenizer unavailable")
    vault, thai, _english = _fixture(tmp_path)
    cases = [EvalCase(query=THAI_PARTIAL_QUERY, relevant_ids=(thai.id,), language="thai")]

    result = evaluate_lexical(vault, cases, tokenizer="trigram")

    assert result["per_case"][0]["rank"] == 0
    assert result["recall_at_1"] == 1.0


def test_english_control_hits_both_tokenizers(tmp_path) -> None:
    if not TRIGram_AVAILABLE:
        pytest.skip("SQLite < 3.34: trigram tokenizer unavailable")
    vault, _thai, english = _fixture(tmp_path)
    cases = [EvalCase(query="fictional weave", relevant_ids=(english.id,), language="english")]

    for tokenizer in ("unicode61", "trigram"):
        result = evaluate_lexical(vault, cases, tokenizer=tokenizer)
        assert result["recall_at_1"] == 1.0, tokenizer


def test_compare_tokenizers_side_by_side(tmp_path) -> None:
    vault, thai, english = _fixture(tmp_path)
    cases = [
        EvalCase(query=THAI_PARTIAL_QUERY, relevant_ids=(thai.id,), language="thai"),
        EvalCase(query="fictional weave", relevant_ids=(english.id,), language="english"),
    ]

    comparison = compare_tokenizers(vault, cases)

    assert comparison["cases"] == 2
    unicode61 = comparison["tokenizers"]["unicode61"]
    assert "skipped" not in unicode61
    assert unicode61["by_language"]["english"]["recall_at_1"] == 1.0
    assert unicode61["by_language"]["thai"]["recall_at_10"] == 0.0
    trigram = comparison["tokenizers"]["trigram"]
    if TRIGram_AVAILABLE:
        assert "skipped" not in trigram
        assert trigram["by_language"]["thai"]["recall_at_1"] == 1.0
    else:
        assert "skipped" in trigram


def test_evaluation_is_deterministic(tmp_path) -> None:
    vault, thai, english = _fixture(tmp_path)
    cases = [
        EvalCase(query=THAI_PARTIAL_QUERY, relevant_ids=(thai.id,), language="thai"),
        EvalCase(query="fictional weave", relevant_ids=(english.id,), language="english"),
    ]

    assert evaluate_lexical(vault, cases) == evaluate_lexical(vault, cases)


def test_evaluation_rejects_bad_inputs(tmp_path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(RetrievalEvalError):
        evaluate_lexical(tmp_path / "nowhere", [])
    with pytest.raises(RetrievalEvalError):
        evaluate_lexical(vault, [], tokenizer="unicode61; DROP TABLE x")


def test_known_item_cases_from_vault(tmp_path) -> None:
    vault, thai, english = _fixture(tmp_path)

    cases = known_item_cases_from_vault(vault)

    by_language = {}
    for case in cases:
        by_language.setdefault(case.language, []).append(case)
    assert [c.relevant_ids for c in by_language["thai"]] == [(thai.id,)]
    assert [c.relevant_ids for c in by_language["english"]] == [(english.id,)]
    assert by_language["thai"][0].query == THAI_TITLE
    # deterministic order
    assert cases == known_item_cases_from_vault(vault)
