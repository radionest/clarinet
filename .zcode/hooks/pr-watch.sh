#!/bin/bash
# Background PR monitor — polls GitHub CI checks and writes report.
# Usage: pr-watch.sh <PR_NUMBER> [max_iterations] [interval_seconds] [owner/repo]
#
# Spawned by pr-monitor.sh PostToolUse hook. Runs detached from Claude session.
# Writes final report to /tmp/pr-<N>-report.md when all checks complete.

set -euo pipefail

PR_NUM="${1:?Usage: pr-watch.sh <PR_NUMBER> [max_iter] [interval] [owner/repo]}"
MAX_ITER="${2:-12}"
INTERVAL="${3:-300}"
REPO="${4:-}"
REPORT="/tmp/pr-${PR_NUM}-report.md"

# Resolve owner/repo once at startup: this process is detached and may outlive
# its cwd (worktree removal), after which gh cannot infer the repo from the
# directory anymore.
if [ -z "$REPO" ]; then
  REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null) || REPO=""
fi
if [ -z "$REPO" ]; then
  {
    echo "# PR #${PR_NUM} — Monitoring failed ($(date -Iseconds))"
    echo "Cannot resolve owner/repo: no argument given and gh repo view failed."
  } > "$REPORT"
  exit 1
fi

CHECKS=""
STATUS_JSON=""
GH_FAILS=0

# Count a probe failure; abort with an error report after 3 consecutive ones —
# a gh/jq failure must never be mistaken for "all checks completed".
note_failure() {
  GH_FAILS=$((GH_FAILS + 1))
  echo "Iteration ${i}/${MAX_ITER}: $1 (${GH_FAILS} consecutive) ($(date -Iseconds))" >> "$REPORT"
  if [ "$GH_FAILS" -ge 3 ]; then
    {
      echo "# PR #${PR_NUM} — Monitoring aborted ($(date -Iseconds))"
      echo ""
      echo "Probe failed ${GH_FAILS} times in a row. Last output:"
      echo '```'
      printf '%s\n' "$2"
      echo '```'
    } > "$REPORT"
    exit 1
  fi
}

# Write initial status
echo "# PR #${PR_NUM} — Monitoring started $(date -Iseconds)" > "$REPORT"
echo "Checking every ${INTERVAL}s, max ${MAX_ITER} iterations." >> "$REPORT"

for ((i=1; i<=MAX_ITER; i++)); do
    sleep "$INTERVAL"

    # Machine-readable probe gates the loop; exit code and JSON shape are both
    # checked so a gh failure (network, rate limit, dead cwd) never reads as done.
    if ! STATUS_JSON=$(gh -R "$REPO" pr view "$PR_NUM" --json statusCheckRollup,comments,reviews,state 2>&1); then
        note_failure "gh pr view failed" "$STATUS_JSON"
        continue
    fi
    PENDING=$(printf '%s' "$STATUS_JSON" | jq '[.statusCheckRollup[]? | select((.status? // .state? // "") | test("QUEUED|IN_PROGRESS|PENDING|EXPECTED"))] | length' 2>/dev/null) || PENDING=""
    if [ -z "$PENDING" ]; then
        note_failure "unparseable gh pr view payload" "$STATUS_JSON"
        continue
    fi
    GH_FAILS=0

    if [ "$PENDING" -eq 0 ]; then
        # All done — fetch the human-readable table for the report (not for gating)
        CHECKS=$(gh -R "$REPO" pr checks "$PR_NUM" 2>&1) || true
        cat > "$REPORT" <<REPORT_EOF
# PR #${PR_NUM} — CI Complete ($(date -Iseconds))

## Check Results
\`\`\`
${CHECKS}
\`\`\`

## Reviews & Comments
\`\`\`json
${STATUS_JSON}
\`\`\`
REPORT_EOF
        exit 0
    fi

    # Still pending — update status
    echo "Iteration ${i}/${MAX_ITER}: ${PENDING} checks pending ($(date -Iseconds))" >> "$REPORT"
done

# Timeout — write what we have
cat > "$REPORT" <<REPORT_EOF
# PR #${PR_NUM} — Monitoring Timeout ($(date -Iseconds))

Gave up after ${MAX_ITER} iterations ($(( MAX_ITER * INTERVAL / 60 )) minutes).

## Last Check Results
\`\`\`
${CHECKS:-no data}
\`\`\`

## Reviews & Comments
\`\`\`json
${STATUS_JSON:-no data}
\`\`\`
REPORT_EOF
