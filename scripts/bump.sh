#!/bin/bash
# Bump version in pyproject.toml, commit, tag, and push.
#
# Usage:
#   scripts/bump.sh          # auto-increment alpha number (0.0a.19 → 0.0a.20)
#   scripts/bump.sh 0.0a.25  # set explicit version
#
# Must be on main branch with clean working tree.

set -euo pipefail

PYPROJECT="pyproject.toml"

if [ ! -f "$PYPROJECT" ]; then
  echo "Error: $PYPROJECT not found" >&2
  exit 1
fi

BRANCH=$(git branch --show-current)
if [ "$BRANCH" != "main" ]; then
  echo "Error: must be on main branch (current: $BRANCH)" >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Error: working tree is not clean" >&2
  git status --short >&2
  exit 1
fi

CURRENT=$(grep -oP '^version\s*=\s*"\K[^"]+' "$PYPROJECT")
if [ -z "$CURRENT" ]; then
  echo "Error: could not read version from $PYPROJECT" >&2
  exit 1
fi

if [ $# -ge 1 ]; then
  NEW="$1"
else
  # Auto-increment: 0.0a.19 → 0.0a.20
  PREFIX=$(echo "$CURRENT" | grep -oP '^.*\.')
  NUM=$(echo "$CURRENT" | grep -oP '\d+$')
  NEW="${PREFIX}$((NUM + 1))"
fi

if [ "$NEW" = "$CURRENT" ]; then
  echo "Error: new version ($NEW) is same as current ($CURRENT)" >&2
  exit 1
fi

echo "$CURRENT → $NEW"

sed -i "s/^version = \"$CURRENT\"/version = \"$NEW\"/" "$PYPROJECT"

git add "$PYPROJECT"
git commit -m "chore: version bump to $NEW"
git tag "v$NEW"
git push
git push --tags

echo "Done: v$NEW pushed"
