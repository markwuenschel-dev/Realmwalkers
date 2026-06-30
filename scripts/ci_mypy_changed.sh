#!/usr/bin/env bash
# Mypy on Python files changed vs merge base (optional full strict check).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

mapfile -t files < <(bash scripts/ci_changed_py_files.sh "${1:-origin/main}")

if [[ ${#files[@]} -eq 0 ]]; then
  echo "mypy: no changed Python files — skipping"
  exit 0
fi

echo "mypy: checking ${#files[@]} changed file(s):"
printf '  %s\n' "${files[@]}"

uv run --no-sync mypy "${files[@]}"
