#!/usr/bin/env bash
# Pyright on Python files changed vs merge base — same scope as CI static job.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

mapfile -t files < <(bash scripts/ci_changed_py_files.sh "${1:-origin/main}")

if [[ ${#files[@]} -eq 0 ]]; then
  echo "pyright: no changed Python files — skipping"
  exit 0
fi

echo "pyright: checking ${#files[@]} changed file(s):"
printf '  %s\n' "${files[@]}"

if command -v uv >/dev/null 2>&1 && uv run --no-sync pyright "${files[@]}"; then
  exit 0
fi
python -m pyright "${files[@]}"
