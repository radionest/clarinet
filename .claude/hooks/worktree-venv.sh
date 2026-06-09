#!/bin/bash
# Ensure worktree has no stale .venv symlink (uv run will create .venv on first use)

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
GIT_COMMON_DIR=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0

# Only act in worktrees
[ "$GIT_DIR" = "$GIT_COMMON_DIR" ] && exit 0

# Real .venv already exists — skip
[ -d ".venv" ] && [ -f ".venv/pyvenv.cfg" ] && exit 0

# Remove stale symlink
[ -L ".venv" ] && rm -f .venv
