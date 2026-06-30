#!/usr/bin/env bash
# Print changed Python files under src/ (one path per line).
# Usage: scripts/ci_changed_py_files.sh [base-ref]
set -euo pipefail

resolve_diff_range() {
  if [[ "${GITHUB_EVENT_NAME:-}" == "push" && -n "${GITHUB_EVENT_BEFORE:-}" && "${GITHUB_EVENT_BEFORE}" != "0000000000000000000000000000000000000000" ]]; then
    printf '%s..HEAD' "${GITHUB_EVENT_BEFORE}"
    return
  fi
  local base_ref="${1:-origin/main}"
  if [[ -n "${GITHUB_BASE_REF:-}" ]]; then
    git fetch origin "${GITHUB_BASE_REF}" --depth=1 2>/dev/null || true
    base_ref="origin/${GITHUB_BASE_REF}"
  fi
  printf '%s...HEAD' "${base_ref}"
}

BASE_ARG="${1:-origin/main}"
RANGE="$(resolve_diff_range "${BASE_ARG}")"

mapfile -t py_files < <(
  git diff --name-only --diff-filter=ACMR "${RANGE}" -- 'src/' \
    | grep -E '\.py$' \
    | sort -u \
    || true
)

for f in "${py_files[@]}"; do
  if [[ -f "${f}" ]]; then
    echo "${f}"
  fi
done
