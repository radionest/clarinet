#!/bin/bash
# PostToolUse hook: detect `gh pr create`, start background monitoring,
# and force Claude to acknowledge via exit 2.
# Fires on every Bash call but exits immediately if not PR creation.

INPUT=$(cat)

# Quick exit: not a gh pr create command
echo "$INPUT" | grep -q "gh pr create" || exit 0

# Exclude dry-run, help, and list commands
echo "$INPUT" | grep -qE "(--help|--dry-run|gh pr create\b.*\blist)" && exit 0

# Extract PR URL from output
PR_URL=$(echo "$INPUT" | grep -oP 'https://github\.com/[^"]+/pull/\d+' | head -1)
[ -z "$PR_URL" ] && exit 0

PR_NUM=$(echo "$PR_URL" | grep -oP '\d+$')
REPORT="/tmp/pr-${PR_NUM}-report.md"
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"

# Start background monitoring (self-contained, no Claude needed)
nohup "$HOOK_DIR/pr-watch.sh" "$PR_NUM" 12 300 > /dev/null 2>&1 &

# Inform Claude about the PR and report location
cat >&2 <<EOF
PR_CREATED: PR #${PR_NUM} (${PR_URL}).
Background CI monitor started (PID $!, polling every 5 min, max 1 hour).
Report: ${REPORT}
When the user asks about PR status, read ${REPORT}.
EOF

exit 2
