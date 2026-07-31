#!/usr/bin/env python3
"""Script-only graph-delta watchdog for a Constellation vault.

Takes a snapshot of the canonical graph, diffs it against the most recent
prior snapshot (if any), and prints a single JSON alert line ONLY when the
graph changed — silent when nothing changed, so it is safe to schedule as
a no_agent cron watchdog (empty stdout = silent). Exits non-zero on error.

Usage: python scripts/graph_delta_watchdog.py /path/to/vault
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from constellation.graph_delta import diff_snapshots, snapshot_graph  # noqa: E402

_SNAPSHOT_DIR = ".constellation/graph-snapshots"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: graph_delta_watchdog.py VAULT", file=sys.stderr)
        return 2
    vault = Path(sys.argv[1]).expanduser()
    try:
        current = snapshot_graph(vault)
    except Exception as exc:  # noqa: BLE001 — watchdog must alert, not crash silently
        print(json.dumps({"alert": "graph_delta_watchdog_error", "error": str(exc)}))
        return 1

    snapshots = sorted(
        (vault / _SNAPSHOT_DIR).glob("snapshot-*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    prior = [
        path for path in snapshots
        if path.name != Path(current["snapshot_path"]).name
    ]
    if not prior:
        # First run: baseline established, nothing to compare. Stay silent.
        return 0
    diff = diff_snapshots(
        vault, f"{_SNAPSHOT_DIR}/{prior[-1].name}", current["snapshot_path"]
    )
    totals = diff["totals"]
    if totals["added"] or totals["removed"] or totals["changed"]:
        print(json.dumps({
            "alert": "canonical_graph_changed",
            "added": totals["added"],
            "removed": totals["removed"],
            "changed": totals["changed"],
            "unchanged": totals["unchanged"],
            "receipt": diff["receipt_path"],
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
