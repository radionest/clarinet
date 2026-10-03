#!/bin/bash
# PreToolUse hook for Agent: blocks analysis and development agents on main.
# Forces entering a worktree first so analysis and the follow-up edits share one context.

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

BRANCH=$(git branch --show-current 2>/dev/null)
[ "$BRANCH" != "main" ] && [ "$BRANCH" != "master" ] && exit 0

# Read tool input from stdin
INPUT=$(cat)

# jq, not grep: grep-based extraction breaks on escaped quotes in the payload.
SUBAGENT=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null)

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
