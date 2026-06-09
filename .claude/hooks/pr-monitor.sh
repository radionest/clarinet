#!/bin/bash
# PostToolUse hook: detect `gh pr create`, start background monitoring,
# and force Claude to acknowledge via exit 2.
# Fires on every Bash call but exits immediately if not PR creation.

INPUT=$(cat)

# Match only the executed command — tool output that merely mentions
# `gh pr create` (gh pr view, git log, reading docs) must not trigger.
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$COMMAND" ] && exit 0
printf '%s' "$COMMAND" | grep -q "gh pr create" || exit 0

# Exclude dry-run and help invocations
printf '%s' "$COMMAND" | grep -qE -- '--help|--dry-run' && exit 0

# Extract the PR URL from the command output, not from the whole payload
PR_URL=$(printf '%s' "$INPUT" | jq -r '.tool_response | tostring' 2>/dev/null | grep -oP 'https://github\.com/[^/\s"\\]+/[^/\s"\\]+/pull/\d+' | head -1)
[ -z "$PR_URL" ] && exit 0

PR_NUM=$(echo "$PR_URL" | grep -oP '\d+$')
# Pass owner/repo to the watcher: the detached process outlives this hook's cwd
REPO=$(echo "$PR_URL" | grep -oP 'github\.com/\K[^/]+/[^/]+')
REPORT="/tmp/pr-${PR_NUM}-report.md"
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"

# Start background monitoring (self-contained, no Claude needed)
nohup "$HOOK_DIR/pr-watch.sh" "$PR_NUM" 12 300 "$REPO" > /dev/null 2>&1 &

# Inform Claude about the PR and report location
cat >&2 <<EOF
PR_CREATED: PR #${PR_NUM} (${PR_URL}).
Background CI monitor started (PID $!, polling every 5 min, max 1 hour).
Report: ${REPORT}
When the user asks about PR status, read ${REPORT}.
EOF

exit 2
