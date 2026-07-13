#!/usr/bin/env python3
"""Audit a candidate public release tree for clean-room leaks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from constellation.privacy import audit_tree  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--canary", action="append", default=[])
    args = parser.parse_args()
    report = audit_tree(args.root, args.canary)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
