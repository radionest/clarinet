#!/usr/bin/env bash
# Wrapper around pytest that prints a reliable JSON summary after the run.
# Usage: ./scripts/run_tests.sh [pytest args...]
# Example: ./scripts/run_tests.sh -n auto --dist loadgroup
set -uo pipefail

# Overridable only so tests/test_run_tests_script.py can point at a stub report;
# pytest itself always writes the path set in pyproject.toml addopts.
REPORT="${CLARINET_TEST_REPORT:-/tmp/clarinet-test-report.json}"

uv run pytest "$@"
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
