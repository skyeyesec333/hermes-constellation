#!/usr/bin/env python3
"""Wave 5 operator journey — deterministic end-to-end proof on a scratch vault.

Runs the canonical private journey through the actual operator surface (the
CLI), exiting non-zero on any failure. This is the regression harness for
"the product works" — replayable, no network, no paid calls.

W5 finding recorded here: there is no CLI entity-create command. Entities
enter the vault via import/migration tooling, so this journey seeds two
canonical entity records the same way (direct canonical write), then drives
every subsequent step through the CLI:

  init -> ingest -> claim stage -> review list -> graph-surface ->
  graph neighbors --typed -> briefing -> timeline-surface ->
  validate -> doctor -> health -> hybrid search

Usage: PYTHONPATH=src python scripts/operator_journey.py [--keep] [--workdir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _seed_entities(vault: Path) -> tuple[str, str]:
    """Write two canonical entity records (import-tooling path; no CLI exists)."""
    from constellation.frontmatter import render_frontmatter
    from constellation.models import EntityKind, EntityRecord, Sensitivity, generate_ulid

    ids = []
    for title in ("JourneyCo", "PartnerCo"):
        record = EntityRecord(
            id=generate_ulid(), type=EntityKind.COMPANY, title=title,
            status="active", sensitivity=Sensitivity.INTERNAL, source_ids=[],
            created_at=NOW, updated_at=NOW,
        )
        (vault / "entities" / f"{record.id}.md").write_text(
            render_frontmatter(record.model_dump(mode="json", exclude_none=True), f"# {title}\n"),
            encoding="utf-8",
        )
        ids.append(record.id)
    return ids[0], ids[1]


def _cli(vault: Path, *args: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "constellation.cli", *args],
        capture_output=True, text=True, cwd=REPO, env=env, timeout=120,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"non-JSON output (rc={proc.returncode}): {proc.stdout[:200]} {proc.stderr[:200]}"}
    if proc.returncode != 0 or not payload.get("ok"):
        return {"ok": False, "error": json.dumps(payload)[:300]}
    return payload.get("result", {})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="Keep the scratch vault")
    parser.add_argument("--workdir", type=Path, default=None)
    args = parser.parse_args()

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="constellation-journey-"))
    workdir.mkdir(parents=True, exist_ok=True)
    vault = workdir / "vault"
    failures: list[str] = []

    def step(name: str, fn) -> dict:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 — journey reports, not raises
            failures.append(f"{name}: exception {exc}")
            print(f"FAIL {name}: {exc}")
            return {}
        if isinstance(result, dict) and result.get("ok") is False:
            failures.append(f"{name}: {result.get('error')}")
            print(f"FAIL {name}: {result.get('error')}")
            return {}
        print(f"PASS {name}")
        return result if isinstance(result, dict) else {}

    step("init", lambda: _cli(vault, "init", str(vault)))

    # real-vault operator config: automatic source registration (default is
    # review-gated, which stages source-items as candidates instead)
    config_path = vault / ".constellation" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "source_registration: review", "source_registration: automatic"
        ),
        encoding="utf-8",
    )
    seed = step("seed-entities", lambda: {"ids": _seed_entities(vault)})
    if not seed:
        print("JOURNEY FAILED: seed-entities")
        return 1
    entity_a, entity_b = seed["ids"]

    sample = vault / "Inbox" / "sample.md"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text("# Journey source\n\nJourneyCo partnered with PartnerCo in 2026.\n", encoding="utf-8")
    step("ingest", lambda: _cli(vault, "ingest", str(vault), str(sample)))
    sources = sorted((vault / "source-items").glob("*.md"))
    if not sources:
        print("FAIL source-item-materialized: no source-items after ingest")
        failures.append("source-item-materialized")
        source_id = ""
    else:
        print("PASS source-item-materialized")
        source_id = sources[0].stem

    step("claim-stage", lambda: _cli(
        vault, "claim", str(vault), "stage",
        "--subject-id", entity_a, "--predicate", "partners_with",
        "--object-id", entity_b, "--source-ids", source_id,
    ))
    review = step("review-list", lambda: _cli(vault, "review", str(vault), "list"))
    if review and not any("claim" in str(c) for c in review.get("candidates", [])):
        failures.append("review-list: staged claim candidate not visible")
        print("FAIL review-list: staged claim candidate not visible")

    graph_html = workdir / "graph.html"
    step("graph-surface", lambda: _cli(
        vault, "graph-surface", str(vault), "--output", str(graph_html)))
    step("graph-neighbors-typed", lambda: _cli(
        vault, "graph", str(vault), "neighbors", "--entity", entity_a, "--typed"))
    briefing = workdir / "briefing.md"
    step("briefing", lambda: _cli(
        vault, "briefing", str(vault), entity_a, "--out", str(briefing)))
    if briefing.exists() and "CANDIDATE" not in briefing.read_text(encoding="utf-8"):
        failures.append("briefing: staged candidate not flagged")
        print("FAIL briefing: staged candidate not flagged")

    timeline = workdir / "timeline.html"
    step("timeline-surface", lambda: _cli(
        vault, "timeline-surface", str(vault), entity_a, "--output", str(timeline)))

    step("validate", lambda: _cli(vault, "validate", str(vault)))
    step("doctor", lambda: _cli(vault, "doctor", str(vault)))
    step("health", lambda: _cli(vault, "health", str(vault)))
    step("hybrid-search", lambda: _cli(vault, "hybrid", str(vault), "JourneyCo"))

    print(f"workdir: {workdir}{' (kept)' if args.keep else ''}")
    if failures:
        print(f"JOURNEY FAILED ({len(failures)}): {failures}")
        return 1
    print("JOURNEY PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
