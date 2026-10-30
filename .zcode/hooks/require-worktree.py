#!/usr/bin/env python3
"""PreToolUse(Edit|Write): block edits on main/master; harness infra exempt.

Exemptions mirror .claude/hooks/require-worktree.sh so both clients enforce
the same rule: .zcode/, .claude/, openspec/, root AGENTS.md/CLAUDE.md,
.gitignore, .github/.
"""

import _hooklib

MAIN_BRANCHES = ("main", "master")
BLOCK_MESSAGE = (
    "BLOCKED: Editing files on the main branch is not allowed.\n"
    "Work on a feature branch in a git worktree before making changes "
    "(see the using-git-worktrees workflow)."
)


def main() -> None:
    if not _hooklib.in_worktree():
        _hooklib.allow()

    file_path = _hooklib.payload().get("tool_input", {}).get("file_path", "")
    if file_path:
        root = _hooklib.git("rev-parse", "--show-toplevel")
        if root:
            if file_path.startswith(f"{root}/.zcode/"):
                _hooklib.allow()  # infra files always allowed
            if file_path.startswith(f"{root}/.claude/"):
                _hooklib.allow()  # back-compat with .claude/ infra
            if file_path.startswith(f"{root}/openspec/"):
                _hooklib.allow()  # OpenSpec planning scratch
            if file_path in (f"{root}/AGENTS.md", f"{root}/CLAUDE.md"):
                _hooklib.allow()  # harness canon — edit on main
            if file_path == f"{root}/.gitignore" or file_path.startswith(f"{root}/.github/"):
                _hooklib.allow()  # harness infra — edit on main
            if not file_path.startswith(f"{root}/"):
                _hooklib.allow()  # file outside repo — skip

    if _hooklib.current_branch() in MAIN_BRANCHES:
        _hooklib.block(BLOCK_MESSAGE)

    _hooklib.allow()


if __name__ == "__main__":
    main()
