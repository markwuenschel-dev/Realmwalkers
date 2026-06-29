#!/usr/bin/env bash
# Copy .agents/skills/ → .cursor/skills/ for Cursor's slash menu.
# Run locally after pull or skill updates. Do not commit .cursor/skills/ (gitignored).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
src="$root/.agents/skills"
dst="$root/.cursor/skills"
mkdir -p "$dst"
for skill in "$src"/*/; do
  name="$(basename "$skill")"
  rm -rf "$dst/$name"
  cp -a "$skill" "$dst/$name"
done
echo "Synced $(find "$dst" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') skills to .cursor/skills/"
