#!/bin/bash
# PreToolUse hook for Bash: blocks branch switching in the project root checkout.
# Use EnterWorktree to work on another branch.

INPUT=$(cat)
# jq, not grep: grep-based extraction stops at the first escaped quote and
# lets commands like `echo "x" && git checkout main` slip through unseen.
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$COMMAND" ] && exit 0

# Match checkout/switch only in git-subcommand position — pipes and arguments
# like `git log --oneline | grep checkout` must not trigger.
if ! printf '%s' "$COMMAND" | grep -qP '(^|[^[:alnum:]_./-])git\s+(-C\s+\S+\s+|-c\s+\S+\s+|--?[[:alnum:]-]+(=\S*)?\s+)*(checkout|switch)\b'; then
  exit 0
fi

# Allow file restore from any ref: `git checkout -- <file>`, `git checkout <ref> -- <file>`
if printf '%s' "$COMMAND" | grep -qP '(checkout|switch)\b[^|;&]*\s--(\s|$)'; then
  exit 0
fi

# Extract the -C path if present (git -C <path> checkout ...)
TARGET_DIR=$(echo "$COMMAND" | grep -oP 'git\s+-C\s+\K\S+' || true)

if [ -n "$TARGET_DIR" ]; then
  # Inspect the target repository
  GIT_DIR=$(git -C "$TARGET_DIR" rev-parse --git-dir 2>/dev/null) || exit 0
  COMMON_DIR=$(git -C "$TARGET_DIR" rev-parse --git-common-dir 2>/dev/null) || exit 0
else
  # Inspect the CWD repository
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
  GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
  COMMON_DIR=$(git rev-parse --git-common-dir 2>/dev/null)
fi

if [ "$(realpath "$GIT_DIR")" = "$(realpath "$COMMON_DIR")" ]; then
  cat >&2 <<'EOF'
BLOCKED: Смена ветки в корневой директории проекта запрещена.
Используй EnterWorktree для работы на другой ветке.
`git checkout -- <file>` для восстановления файлов по-прежнему доступен.
EOF
  exit 2
fi

exit 0
