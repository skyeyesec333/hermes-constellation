#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="${CONSTELLATION_PYTHON:-$ROOT/.venv/bin/python}"
else
  PYTHON="${CONSTELLATION_PYTHON:-python3}"
fi

TMP="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

"$ROOT/scripts/verify_fast.sh"

printf '\n==> build wheel\n'
"$PYTHON" -m build --wheel --no-isolation --outdir "$TMP/dist"
WHEEL="$(find "$TMP/dist" -maxdepth 1 -type f -name '*.whl' -print -quit)"
if [[ -z "$WHEEL" ]]; then
  printf 'No wheel was produced\n' >&2
  exit 1
fi

printf '\n==> compile clean public tree\n'
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" scripts/build_release.py \
  . "$TMP/public" resources/public-lineage.yaml > "$TMP/release-report.json"

printf '\n==> audit compiled tree\n'
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" scripts/privacy_audit.py "$TMP/public" > "$TMP/privacy-report.json"

printf '\n==> create fresh smoke environment\n'
python3 -m venv "$TMP/smoke-venv"
"$TMP/smoke-venv/bin/python" -m pip install --disable-pip-version-check --quiet "$WHEEL"

printf '\n==> smoke installed CLI\n'
"$TMP/smoke-venv/bin/constellation" --help >/dev/null
"$TMP/smoke-venv/bin/constellation" init "$TMP/vault" >/dev/null
"$TMP/smoke-venv/bin/constellation" doctor "$TMP/vault" > "$TMP/doctor.json"
"$TMP/smoke-venv/bin/constellation" validate "$TMP/vault" > "$TMP/validate.json"

"$PYTHON" - "$TMP/release-report.json" "$TMP/privacy-report.json" "$TMP/doctor.json" "$TMP/validate.json" <<'PY'
import json
import sys
from pathlib import Path

release = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
privacy = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
doctor = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
validation = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))

if release["audit"]["passed"] is not True or privacy["passed"] is not True:
    raise SystemExit("release privacy audit did not pass")
if doctor.get("ok") is not True:
    raise SystemExit("fresh installed vault doctor did not report ok")
if validation.get("ok") is not True or validation.get("result", {}).get("invalid") != 0:
    raise SystemExit("fresh installed vault contains invalid canonical records")

print(
    "RELEASE VERIFICATION PASSED "
    f"files={release['file_count']} tree_sha256={release['tree_sha256']}"
)
PY
