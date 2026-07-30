"""Retrieval evaluation — measures lexical FTS5 quality, per tokenizer.

Thai script has no inter-word whitespace, so the default unicode61 tokenizer
cannot segment natural Thai text; this harness quantifies that gap (and any
alternative tokenizer's recovery) using known-item-search cases: each case
pairs a query with the record ids that SHOULD be retrieved, then measures
recall@k and MRR against a disposable in-memory index built with the same
document extraction as the production index. Read-only: no canonical
mutation, no state writes, no network.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .retrieval import _canonical_files
from .validation import validate_canonical_text
from .vault import is_initialized


class RetrievalEvalError(RuntimeError):
    """Raised when an evaluation cannot run."""


@dataclass(frozen=True)
class EvalCase:
    """One known-item-search case: query plus the ids it should retrieve."""

    query: str
    relevant_ids: tuple[str, ...]
    language: str = "unknown"


def first_relevant_rank(
    ranked_ids: list[str], relevant_ids: tuple[str, ...] | set[str],
) -> int | None:
    """0-based rank of the first relevant id in the ranked list, or None."""
    relevant = set(relevant_ids)
    for rank, note_id in enumerate(ranked_ids):
        if note_id in relevant:
            return rank
    return None


def recall_at(rank: int | None, k: int) -> float:
    return 1.0 if rank is not None and rank < k else 0.0


def mrr(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / (rank + 1)


def _match_expression(query: str) -> str | None:
    """Mirror retrieval.search: unicode \\w terms, AND-joined quoted phrases."""
    terms = re.findall(r"[\w-]+", query, flags=re.UNICODE)
    if not terms:
        return None
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:20])


def _build_eval_index(vault: Path, tokenizer: str) -> sqlite3.Connection:
    """Disposable in-memory FTS5 index with the production doc extraction."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE VIRTUAL TABLE search_index USING fts5("
        f"note_id UNINDEXED, title, body, tokenize='{tokenizer}')"
    )
    for path in _canonical_files(vault):
        relative = path.relative_to(vault).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            record = validate_canonical_text(text, relative)
            metadata, body = parse_frontmatter(text)
        except Exception:
            continue
        note_id = str(getattr(record, "id", metadata.get("id", "")))
        connection.execute(
            "INSERT INTO search_index VALUES (?, ?, ?)",
            (note_id, str(metadata["title"]), body),
        )
    return connection


def _aggregate(per_case: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(per_case)
    if total == 0:
        return {"cases": 0, "recall_at_1": 0.0, "recall_at_5": 0.0,
                "recall_at_10": 0.0, "mrr": 0.0}
    return {
        "cases": total,
        "recall_at_1": round(sum(c["recall_at_1"] for c in per_case) / total, 4),
        "recall_at_5": round(sum(c["recall_at_5"] for c in per_case) / total, 4),
        "recall_at_10": round(sum(c["recall_at_10"] for c in per_case) / total, 4),
        "mrr": round(sum(c["mrr"] for c in per_case) / total, 4),
    }


def evaluate_lexical(
    root: Path | str,
    cases: list[EvalCase],
    *,
    tokenizer: str = "unicode61",
    limit: int = 10,
) -> dict[str, Any]:
    """Score known-item-search cases against one FTS5 tokenizer. Read-only."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise RetrievalEvalError("evaluation requires an initialized vault")
    if not re.fullmatch(r"[A-Za-z0-9_ ]+", tokenizer):
        raise RetrievalEvalError(f"unsafe tokenizer name: {tokenizer!r}")

    try:
        connection = _build_eval_index(vault, tokenizer)
    except sqlite3.OperationalError as exc:
        raise RetrievalEvalError(f"tokenizer unavailable: {tokenizer} ({exc})") from exc

    per_case: list[dict[str, Any]] = []
    for case in cases:
        expression = _match_expression(case.query)
        ranked: list[str] = []
        if expression is not None:
            try:
                rows = connection.execute(
                    "SELECT note_id FROM search_index WHERE search_index MATCH ? "
                    "ORDER BY rank LIMIT ?",
                    (expression, limit),
                ).fetchall()
                ranked = [row[0] for row in rows]
            except sqlite3.OperationalError:
                ranked = []
        rank = first_relevant_rank(ranked, case.relevant_ids)
        per_case.append({
            "query": case.query,
            "language": case.language,
            "rank": rank,
            "recall_at_1": recall_at(rank, 1),
            "recall_at_5": recall_at(rank, 5),
            "recall_at_10": recall_at(rank, 10),
            "mrr": mrr(rank),
        })
    connection.close()

    by_language: dict[str, list[dict[str, Any]]] = {}
    for entry in per_case:
        by_language.setdefault(entry["language"], []).append(entry)

    return {
        "tokenizer": tokenizer,
        "limit": limit,
        **_aggregate(per_case),
        "by_language": {
            language: _aggregate(entries)
            for language, entries in sorted(by_language.items())
        },
        "per_case": per_case,
    }


def compare_tokenizers(
    root: Path | str,
    cases: list[EvalCase],
    tokenizers: tuple[str, ...] = ("unicode61", "trigram"),
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """Side-by-side tokenizer comparison; unavailable tokenizers are skipped."""
    results: dict[str, Any] = {}
    for tokenizer in tokenizers:
        try:
            results[tokenizer] = evaluate_lexical(root, cases, tokenizer=tokenizer, limit=limit)
        except RetrievalEvalError as exc:
            results[tokenizer] = {"skipped": str(exc)}
    return {"cases": len(cases), "limit": limit, "tokenizers": results}


_THAI_RE = re.compile(r"[฀-๿]")


def known_item_cases_from_vault(
    root: Path | str, *, max_per_language: int = 25,
) -> list[EvalCase]:
    """Auto-build known-item cases: exact-title queries for Thai-script and
    Latin-script titled records (capped per language, deterministic order)."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise RetrievalEvalError("evaluation requires an initialized vault")
    thai: list[EvalCase] = []
    latin: list[EvalCase] = []
    for path in _canonical_files(vault):
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            title = str(metadata.get("title", "")).strip()
            note_id = str(metadata.get("id", ""))
        except Exception:
            continue
        if not title or not note_id:
            continue
        bucket = thai if _THAI_RE.search(title) else latin
        bucket.append(EvalCase(query=title, relevant_ids=(note_id,),
                               language="thai" if bucket is thai else "english"))
    thai.sort(key=lambda c: (c.query.casefold(), c.relevant_ids))
    latin.sort(key=lambda c: (c.query.casefold(), c.relevant_ids))
    return thai[:max_per_language] + latin[:max_per_language]
