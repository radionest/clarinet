---
paths:
  - "tests/**"
  - "scripts/run_tests.sh"
---

# Debugging Test Failures

Always capture output on the first run — never re-run tests just to see logs.

## Run tests

```bash
make test-fast                    # JSON report → /tmp/clarinet-test-report.json
CLARINET_LOG_DIR=/tmp make test-fast  # + app logs → /tmp/clarinet.log
make test-debug                   # both at once
```

## Analyze test failures (jq)

```bash
# Failed tests — names + error messages
jq '.tests[] | select(.outcome == "failed") | {nodeid, message: .call.longrepr}' /tmp/clarinet-test-report.json

# Just the names of failed tests
jq -r '.tests[] | select(.outcome == "failed") .nodeid' /tmp/clarinet-test-report.json

# Test durations (slowest first)
jq '[.tests[] | {nodeid, duration}] | sort_by(-.duration) | .[:10]' /tmp/clarinet-test-report.json

# Summary
jq '.summary' /tmp/clarinet-test-report.json
```

## Analyze app logs (jq)

```bash
# App errors
jq 'select(.l == "ERROR")' /tmp/clarinet.log

# Errors with tracebacks
jq 'select(.exc != null)' /tmp/clarinet.log

# Filter by module
jq 'select(.mod | startswith("clarinet.services.pipeline"))' /tmp/clarinet.log
```

## JSON log keys

| Key | Content |
|-----|---------|
| `t` | ISO timestamp |
| `l` | Level (INFO, ERROR, ...) |
| `mod` | Module name |
| `fn` | Function name |
| `line` | Line number |
| `msg` | Log message |
| `exc` | Traceback (only on exceptions) |
