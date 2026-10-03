#!/bin/bash
# Stop hook: blocks ending a session inside a worktree
# so Claude asks the user what to do with the changes.

INPUT=$(cat)

# Prevent an infinite loop — skip on the repeated attempt
if echo "$INPUT" | grep -qE '"stop_hook_active"\s*:\s*true'; then
  exit 0
fi

# Debounce: do not re-block within 120 seconds
# PPID = Claude process — one debounce per session
DEBOUNCE_FILE="/tmp/claude-worktree-stop-${PPID}"
if [ -f "$DEBOUNCE_FILE" ]; then
  LAST=$(cat "$DEBOUNCE_FILE" 2>/dev/null || echo 0)
  NOW=$(date +%s)
  if [ $((NOW - LAST)) -lt 120 ]; then
    exit 0
  fi
fi

# Only act inside a git repository
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Only act in a worktree (not the main checkout)
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
GIT_COMMON_DIR=$(git rev-parse --git-common-dir 2>/dev/null)
if [ "$GIT_DIR" = "$GIT_COMMON_DIR" ]; then
  exit 0
fi

# Collect state information
BRANCH=$(git branch --show-current 2>/dev/null)
HAS_CHANGES=$(git status --porcelain 2>/dev/null | head -1)
AHEAD=$(git rev-list main..HEAD --count 2>/dev/null || echo "0")

# Record the debounce timestamp
date +%s > "$DEBOUNCE_FILE"

# Empty worktree (0 commits, 0 changes) — short message
if [ "$AHEAD" = "0" ] && [ -z "$HAS_CHANGES" ]; then
  echo "WORKTREE_EMPTY: ветка '$BRANCH', нет коммитов и изменений. Оставить или удалить?" >&2
  exit 2
fi

# Block the stop — stderr is shown to Claude
cat >&2 <<EOF
WORKTREE_PENDING: Сессия в worktree, ветка '$BRANCH'.
Коммитов впереди main: $AHEAD
Незакоммиченные изменения: $([ -n "$HAS_CHANGES" ] && echo "есть" || echo "нет")

Перед завершением спроси пользователя (см. CLAUDE.md → Worktree Workflow):
1) Push + PR: commit → push → Agent(pr-diff-reviewer) → gh pr create → ExitWorktree(keep); удалить worktree только после merge PR
2) Оставить worktree для продолжения позже (ExitWorktree(keep))
3) Отменить изменения и удалить worktree (ExitWorktree(remove, discard_changes=true))
EOF

exit 2
