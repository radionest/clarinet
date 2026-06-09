#!/bin/bash
# SessionStart hook: runs `uv sync --dev --extra performance` only if .venv is missing or stale.
# In worktrees the .venv is created by worktree-venv.sh + first uv-run command; this hook handles
# the first session in a fresh clone or after a deliberate rm -rf .venv.

set -euo pipefail

if [ -f ".venv/pyvenv.cfg" ]; then
  exit 0
fi

exec uv sync --dev --extra performance
