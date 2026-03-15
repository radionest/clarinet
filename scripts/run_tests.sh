#!/usr/bin/env bash
# Wrapper around pytest that prints a reliable JSON summary after the run.
# Usage: ./scripts/run_tests.sh [pytest args...]
# Example: ./scripts/run_tests.sh -n auto --dist loadgroup
set -uo pipefail

REPORT="/tmp/clarinet-test-report.json"

uv run pytest "$@"
EXIT=$?

echo ""
echo "=== Test Summary ==="
if command -v jq &>/dev/null && [ -f "$REPORT" ]; then
    jq -r '"passed: \(.summary.passed // 0), failed: \(.summary.failed // 0), skipped: \(.summary.skipped // 0), total: \(.summary.total // 0)"' "$REPORT" 2>/dev/null || echo "(failed to parse JSON report)"
else
    echo "(jq not found or no JSON report at $REPORT)"
fi

exit $EXIT
