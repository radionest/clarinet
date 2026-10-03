#!/bin/bash
# PreToolUse hook: blocks Edit/Write on the main branch.
# Any change requires a worktree or a feature branch.
# Exception: .claude/ — infrastructure configuration.

# Only act inside a git repository
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Skip files outside the repository (plan files, global .claude/, etc.)
INPUT=$(cat)
# jq, not grep: grep-based extraction breaks on escaped quotes in the payload.
FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
if [ -n "$FILE_PATH" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
  case "$FILE_PATH" in
    "$REPO_ROOT"/.claude/*) exit 0 ;;  # .claude/ infra — allowed on main
    "$REPO_ROOT"/openspec/*) exit 0 ;; # planning scratch — always allow
    "$REPO_ROOT"/*) ;;                  # file in repo — keep checking
    *) exit 0 ;;                        # file outside repo — skip
  esac
fi

BRANCH=$(git branch --show-current 2>/dev/null)

if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  cat >&2 <<'EOF'
BLOCKED: Редактирование файлов на ветке main запрещено.
Войди в worktree через EnterWorktree перед внесением изменений.
EOF
  exit 2
fi

exit 0
