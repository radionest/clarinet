#!/bin/bash
# PreToolUse hook for Agent: блокирует аналитических и девелоперских агентов на main.
# Заставляет войти в worktree перед запуском, чтобы анализ и правки были в одном контексте.

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

BRANCH=$(git branch --show-current 2>/dev/null)
[ "$BRANCH" != "main" ] && [ "$BRANCH" != "master" ] && exit 0

# Read tool input from stdin
INPUT=$(cat)

# Extract subagent_type (grep -oP: Perl regex, \K resets match start)
SUBAGENT=$(echo "$INPUT" | grep -oP '"subagent_type"\s*:\s*"\K[^"]*' || true)

# Block development agents on main (read-only agents are allowed)
case "$SUBAGENT" in
  feature-dev:code-explorer|feature-dev:code-reviewer)
    exit 0 ;;  # read-only — no Edit/Write tools
  Plan|python-developer|feature-dev:*)
    cat >&2 <<'EOF'
BLOCKED: Аналитический/архитектурный агент на ветке main.
Войди в worktree через EnterWorktree перед запуском анализа или разработки.
Это обеспечит, что анализ и последующие изменения будут в одном worktree.
EOF
    exit 2
    ;;
esac

exit 0
