#!/usr/bin/env python3
"""Build a public release tree from the repository lineage manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from constellation.release import build_release  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_release(args.source, args.destination, args.manifest), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
