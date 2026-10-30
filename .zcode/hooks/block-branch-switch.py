#!/usr/bin/env python3
"""PreToolUse(Bash): block `git checkout`/`switch` in the root repo,
unless every occurrence is a file-restore (`-- <file>`)."""

import re

import _hooklib

SWITCH_RE = re.compile(r"git\s+(checkout|switch)\b")
RESTORE_RE = re.compile(r"\s--(\s|$)")
BLOCK_MESSAGE = (
    "BLOCKED: Branch switching in the root project directory is not allowed.\n"
    "Work on a different branch in a git worktree instead "
    "(see the using-git-worktrees workflow).\n"
    "`git checkout -- <file>` for restoring files is still available."
)


def switch_segments(command: str) -> list:
    """Segments (split on ; & | and newlines) that contain a git checkout/switch."""
    return [s for s in re.split(r"[;&|\n]", command) if SWITCH_RE.search(s)]


def all_restores(segments: list) -> bool:
    """True if every checkout/switch segment is a file-restore."""
    return all(RESTORE_RE.search(s) for s in segments)


def main() -> None:
    command = _hooklib.payload().get("tool_input", {}).get("command", "")
    if not command:
        _hooklib.allow()

    segments = switch_segments(command)
    if not segments:
        _hooklib.allow()  # no checkout/switch at all
    if all_restores(segments):
        _hooklib.allow()  # every one is `-- <file>`

    if not _hooklib.in_worktree():
        _hooklib.allow()
    if not _hooklib.in_linked_worktree():  # root repo, not a linked worktree
        _hooklib.block(BLOCK_MESSAGE)

    _hooklib.allow()


if __name__ == "__main__":
    main()
