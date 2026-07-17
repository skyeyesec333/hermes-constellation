#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="${CONSTELLATION_PYTHON:-$ROOT/.venv/bin/python}"
else
  PYTHON="${CONSTELLATION_PYTHON:-python3}"
fi

failures=0

run_check() {
  local label="$1"
  shift
  printf '\n==> %s\n' "$label"
  if "$@"; then
    printf 'PASS: %s\n' "$label"
  else
    local status=$?
    printf 'FAIL: %s (exit %s)\n' "$label" "$status" >&2
    failures=1
  fi
}

run_check "git diff whitespace" git diff --check
run_check "ruff" "$PYTHON" -m ruff check .
run_check "pytest" "$PYTHON" -m pytest tests/ -q

if (( failures )); then
  printf '\nFAST VERIFICATION FAILED\n' >&2
  exit 1
fi

printf '\nFAST VERIFICATION PASSED\n'
