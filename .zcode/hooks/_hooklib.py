#!/usr/bin/env python3
"""Shared primitives for the worktree-enforcement hooks.

Each hook does `import _hooklib`; Python puts the hook's own directory on
sys.path[0], so the sibling import resolves without packaging.
"""

import json
import os
import subprocess
import sys
from typing import NoReturn


def payload() -> dict:
    """Parse the hook JSON payload from stdin; {} if empty or invalid."""
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def git(*args: str) -> str:
    """Run a git command; return stripped stdout, or '' on any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def in_worktree() -> bool:
    """True if the CWD is inside any git working tree."""
    return git("rev-parse", "--is-inside-work-tree") == "true"


def in_linked_worktree() -> bool:
    """True if inside a linked worktree (git-dir differs from common-dir)."""
    git_dir = git("rev-parse", "--git-dir")
    common = git("rev-parse", "--git-common-dir")
    return os.path.realpath(git_dir) != os.path.realpath(common)


def current_branch() -> str:
    """Current branch name, or '' if detached/unavailable."""
    return git("branch", "--show-current")


def allow() -> NoReturn:
    """Allow the tool call (exit 0)."""
    sys.exit(0)


def block(message: str) -> NoReturn:
    """Block the tool call: message to stderr, exit 2 (what Claude reads)."""
    print(message, file=sys.stderr)
    sys.exit(2)
