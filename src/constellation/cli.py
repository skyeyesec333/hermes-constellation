"""Command-line interface for the Constellation core."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="constellation")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize a new vault")
    init.add_argument("vault", type=Path)

    doctor = sub.add_parser("doctor", help="Inspect vault health")
    doctor.add_argument("vault", type=Path)

    ingest = sub.add_parser("ingest", help="Preserve a local source and stage its canonical candidate")
    ingest.add_argument("vault", type=Path)
    ingest.add_argument("source", type=Path)

    validate = sub.add_parser("validate", help="Validate canonical records")
    validate.add_argument("vault", type=Path)
    validate.add_argument("--limit", type=int, default=100)

    index = sub.add_parser("index", help="Rebuild the SQLite FTS index")
    index.add_argument("vault", type=Path)

    search = sub.add_parser("search", help="Search canonical evidence")
    search.add_argument("vault", type=Path)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--sensitivity", default="internal")

    review = sub.add_parser("review", help="List or promote candidates")
    review.add_argument("vault", type=Path)
    review.add_argument("action", choices=["list", "promote"])
    review.add_argument("--candidate")
    review.add_argument("--expected-base-hash")
    review.add_argument("--confirm", action="store_true")

    research = sub.add_parser("research", help="Create or inspect a research receipt")
    research.add_argument("vault", type=Path)
    research.add_argument("action", choices=["start", "status"])
    research.add_argument("--run-id")

    migrate = sub.add_parser("migrate-plan", help="Inventory a legacy vault without writing")
    migrate.add_argument("vault", type=Path)
    migrate.add_argument("--action-limit", type=int, default=1_000)
    migrate.add_argument("--max-files", type=int, default=100_000)

    rehearse = sub.add_parser(
        "migrate-rehearse", help="Build a destination-only disposable migration rehearsal"
    )
    rehearse.add_argument("vault", type=Path)
    rehearse.add_argument("destination", type=Path)
    rehearse.add_argument("--max-files", type=int, default=100_000)
    rehearse.add_argument("--confirm-disposable", action="store_true")

    prepare = sub.add_parser(
        "migrate-prepare", help="Build a verified sibling vault for canonical cutover"
    )
    prepare.add_argument("vault", type=Path)
    prepare.add_argument("rehearsal", type=Path)
    prepare.add_argument("destination", type=Path)
    prepare.add_argument("--expected-source-sha256", required=True)
    prepare.add_argument("--confirm-apply-staging", action="store_true")

    activate = sub.add_parser(
        "migrate-activate", help="Atomically activate a prepared vault with rollback"
    )
    activate.add_argument("vault", type=Path)
    activate.add_argument("prepared", type=Path)
    activate.add_argument("rollback", type=Path)
    activate.add_argument("--expected-source-sha256", required=True)
    activate.add_argument("--confirm-canonical-apply", action="store_true")

    return parser


def run_action(action: str, values: dict[str, Any]) -> Any:
    """Dispatch through the shared core. Imports are lazy for plugin startup safety."""
    vault = Path(values["vault"]).expanduser()
    if action == "init":
        from constellation.vault import initialize_vault

        return initialize_vault(vault)
    if action == "doctor":
        from constellation.doctor import doctor_report

        return doctor_report(vault)
    if action == "ingest":
        from constellation.ingest import ingest_file

        return ingest_file(vault, Path(values["source"]).expanduser())
    if action == "validate":
        from constellation.validation import validate_vault

        return validate_vault(vault, limit=int(values.get("limit", 100)))
    if action == "index":
        from constellation.retrieval import build_index

        return build_index(vault)
    if action == "search":
        from constellation.retrieval import search

        return search(
            vault,
            str(values["query"]),
            limit=int(values.get("limit", 10)),
            sensitivity_ceiling=str(values.get("sensitivity", "internal")),
        )
    if action == "review":
        from constellation.review import list_candidates, promote_candidate

        if values.get("action") == "list":
            return list_candidates(vault)
        return promote_candidate(
            vault,
            str(values.get("candidate") or ""),
            confirm=bool(values.get("confirm")),
            expected_base_hash=values.get("expected_base_hash"),
        )
    if action == "research":
        from constellation.research import research_command

        return research_command(vault, values)
    if action == "migrate-plan":
        from constellation.migration import plan_migration

        return plan_migration(
            vault,
            action_limit=int(values.get("action_limit", 1_000)),
            max_files=int(values.get("max_files", 100_000)),
        )
    if action == "migrate-rehearse":
        from constellation.migration import rehearse_migration

        return rehearse_migration(
            vault,
            Path(values["destination"]).expanduser(),
            confirm_disposable=bool(values.get("confirm_disposable")),
            max_files=int(values.get("max_files", 100_000)),
        )
    if action == "migrate-prepare":
        from constellation.apply import build_cutover_vault

        return build_cutover_vault(
            vault,
            Path(values["rehearsal"]).expanduser(),
            Path(values["destination"]).expanduser(),
            expected_source_sha256=str(values["expected_source_sha256"]),
            confirm_apply_staging=bool(values.get("confirm_apply_staging")),
        )
    if action == "migrate-activate":
        from constellation.apply import activate_cutover

        return activate_cutover(
            vault,
            Path(values["prepared"]).expanduser(),
            Path(values["rollback"]).expanduser(),
            expected_source_sha256=str(values["expected_source_sha256"]),
            confirm_canonical_apply=bool(values.get("confirm_canonical_apply")),
        )
    raise ValueError(f"Unknown action: {action}")


def main(argv: Sequence[str] | None = None) -> int:
    args = vars(build_parser().parse_args(argv))
    action = args.pop("command")
    result = run_action(action, args)
    print(json.dumps({"version": 1, "ok": True, "result": result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
