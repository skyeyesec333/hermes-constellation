#!/usr/bin/env python3
"""Retrieval evaluation against a live vault — tokenizer comparison.

Auto-builds known-item-search cases from the vault's own records (exact-title
queries, Thai-script and Latin-script titles, capped per language), then
scores lexical FTS5 recall/MRR under each tokenizer. Read-only: the eval
index is built in memory; nothing touches canonical state.

Usage:
  PYTHONPATH=src python3 scripts/retrieval_eval.py VAULT [--limit N] [--max-cases N] [--json]

Exit codes: 0 = ran clean, 1 = error, plus a one-line VERDICT for humans.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from constellation.retrieval_eval import (  # noqa: E402
    RetrievalEvalError,
    compare_tokenizers,
    known_item_cases_from_vault,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retrieval evaluation against a live vault — tokenizer comparison."
    )
    parser.add_argument("vault", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-cases", type=int, default=25)
    parser.add_argument("--json", action="store_true", help="full JSON output")
    args = parser.parse_args()

    try:
        cases = known_item_cases_from_vault(args.vault, max_per_language=args.max_cases)
    except RetrievalEvalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not cases:
        print("ERROR: no evaluable records found", file=sys.stderr)
        return 1

    comparison = compare_tokenizers(args.vault, cases, limit=args.limit)

    if args.json:
        print(json.dumps(comparison, indent=2, ensure_ascii=False))
        return 0

    print(f"Retrieval evaluation: {comparison['cases']} known-item cases "
          f"(limit {comparison['limit']})")
    for tokenizer, result in comparison["tokenizers"].items():
        if "skipped" in result:
            print(f"\n[{tokenizer}] SKIPPED — {result['skipped']}")
            continue
        print(f"\n[{tokenizer}] overall: recall@1 {result['recall_at_1']:.2f} "
              f"recall@10 {result['recall_at_10']:.2f} MRR {result['mrr']:.3f}")
        for language, agg in result["by_language"].items():
            print(f"  {language:8s} n={agg['cases']:3d} "
                  f"recall@1 {agg['recall_at_1']:.2f} "
                  f"recall@10 {agg['recall_at_10']:.2f} MRR {agg['mrr']:.3f}")

    tokenizers = comparison["tokenizers"]
    unicode61 = tokenizers.get("unicode61", {})
    trigram = tokenizers.get("trigram", {})
    if "skipped" not in unicode61 and "skipped" not in trigram:
        u_thai = unicode61.get("by_language", {}).get("thai", {}).get("recall_at_10", 1.0)
        t_thai = trigram.get("by_language", {}).get("thai", {}).get("recall_at_10", 0.0)
        u_eng = unicode61.get("by_language", {}).get("english", {}).get("recall_at_10", 1.0)
        t_eng = trigram.get("by_language", {}).get("english", {}).get("recall_at_10", 0.0)
        if t_thai > u_thai and t_eng >= u_eng:
            print(f"\nVERDICT: trigram improves Thai recall@10 "
                  f"({u_thai:.2f} -> {t_thai:.2f}) without regressing English "
                  f"({u_eng:.2f} -> {t_eng:.2f}) — tokenizer switch recommended.")
        else:
            print(f"\nVERDICT: no clear trigram win (thai {u_thai:.2f}->{t_thai:.2f}, "
                  f"english {u_eng:.2f}->{t_eng:.2f}) — keep unicode61.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
