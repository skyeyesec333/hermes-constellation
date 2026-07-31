#!/usr/bin/env python3
"""Deterministic, read-only predicate inventory for a Constellation vault.

Scans canonical ``relationships/`` records and entity-to-entity ``claims/``
(those with ``object_id`` set) and reports aggregate predicate counts by
record type. The output is metadata-only: no note bodies, titles, record
IDs, or vault paths appear in it. Identical vault bytes produce
byte-identical output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from constellation.frontmatter import parse_frontmatter

INVENTORY_VERSION = 1


def _scan_predicates(vault: Path, folder: str, *, entity_links_only: bool) -> dict[str, Any]:
    """Return aggregate predicate counts for one canonical folder.

    Files that fail frontmatter parsing or lack a record ``id`` are counted
    as skipped, never raised. Bodies are parsed out but never read into the
    result.
    """
    counts: dict[str, int] = {}
    scanned = 0
    skipped = 0
    base = vault / folder
    if not base.is_dir():
        return {"predicate_counts": {}, "records_scanned": 0, "records_skipped": 0}
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            skipped += 1
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1
            continue
        if not metadata.get("id"):
            skipped += 1
            continue
        if entity_links_only and not metadata.get("object_id"):
            continue
        predicate = str(metadata.get("predicate", "")).strip()
        if not predicate:
            skipped += 1
            continue
        scanned += 1
        counts[predicate] = counts.get(predicate, 0) + 1
    return {
        "predicate_counts": {name: counts[name] for name in sorted(counts)},
        "records_scanned": scanned,
        "records_skipped": skipped,
    }


def inventory_predicates(vault: Path) -> dict[str, Any]:
    """Build the deterministic aggregate inventory for a vault."""
    vault = Path(vault)
    return {
        "version": INVENTORY_VERSION,
        "record_types": {
            "claims_entity_to_entity": _scan_predicates(vault, "claims", entity_links_only=True),
            "relationships": _scan_predicates(vault, "relationships", entity_links_only=False),
        },
    }


def render_inventory(inventory: dict[str, Any]) -> str:
    """Serialize an inventory to canonical JSON (sorted keys, stable bytes)."""
    return json.dumps(inventory, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path, help="path to the Constellation vault (read-only)")
    args = parser.parse_args(argv)
    print(render_inventory(inventory_predicates(args.vault)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
