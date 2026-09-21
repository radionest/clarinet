#!/usr/bin/env bash
# Wrapper around pytest that prints a reliable JSON summary after the run.
# Usage: ./scripts/run_tests.sh [pytest args...]
# Example: ./scripts/run_tests.sh -n auto --dist loadgroup
set -uo pipefail

# Default matches pyproject.toml addopts; an override is passed on to pytest
# below (a CLI option wins over addopts), so the path deleted, written and read
# is always the same one. Callers that run pytest without this wrapper still
# write the default path.
REPORT="${CLARINET_TEST_REPORT:-/tmp/clarinet-test-report.json}"

# Every stage writes the same path and pytest only writes it at session end, so
# a run killed earlier would otherwise be judged by the previous stage's report.
rm -f "$REPORT"

uv run pytest "$@" --json-report-file="$REPORT"
EXIT=$?

echo ""
echo "=== Test Summary ==="
if command -v jq &>/dev/null && [ -f "$REPORT" ]; then
    jq -r '"passed: \(.summary.passed // 0), failed: \(.summary.failed // 0), errors: \(.summary.error // 0), skipped: \(.summary.skipped // 0), total: \(.summary.total // 0)"' "$REPORT" 2>/dev/null || echo "(failed to parse JSON report)"
else
    echo "(jq not found or no JSON report at $REPORT)"
fi

# pytest-xdist workers sometimes get SIGKILL (137) during teardown even when
# all tests passed.  Trust the JSON report over the exit code in that case —
# and only in that case: any other code (3 = INTERNALERROR, the session aborted
# and the tests that never ran are simply missing from the report) stays red,
# and fixture errors count (they land in summary.error, not summary.failed).
if [ "$EXIT" -eq 137 ] && command -v jq &>/dev/null && [ -f "$REPORT" ]; then
    FAILED=$(jq -r '.summary.failed // 0' "$REPORT" 2>/dev/null)
    ERRORS=$(jq -r '.summary.error // 0' "$REPORT" 2>/dev/null)
    PASSED=$(jq -r '.summary.passed // 0' "$REPORT" 2>/dev/null)
    if [ "$FAILED" = "0" ] && [ "$ERRORS" = "0" ] && [ "$PASSED" -gt 0 ] 2>/dev/null; then
        echo "(pytest exited $EXIT but report shows 0 failures — treating as success)"
        EXIT=0
    fi
fi

exit $EXIT
