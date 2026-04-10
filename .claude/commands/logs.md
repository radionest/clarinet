---
description: Analyze Clarinet app logs (JSONL) or test reports (JSON) with jq
argument-hint: <path to log file(s)> [focus: module, time range, error, entity]
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

Analyze Clarinet logs and produce an actionable diagnostic report.

User request: $ARGUMENTS

## Log schemas

### App logs (JSONL) — `clarinet.log`, `clarinet_worker.log`

Each line is a JSON object:

| Key   | Content                          | Example                          |
|-------|----------------------------------|----------------------------------|
| `t`   | ISO timestamp with TZ            | `2026-04-07T12:11:32.090+0300`   |
| `l`   | Level                            | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `mod` | Python module path               | `clarinet.services.dicomweb.cache` |
| `fn`  | Function name                    | `ensure_series_cached`           |
| `line`| Source line number               | `297`                            |
| `msg` | Log message                      | `Memory cache hit for series...` |
| `exc` | Traceback (only on exceptions)   | multi-line string                |

### Test reports (JSON) — `clarinet-test-report.json`

Top-level object with `.tests[]` array and `.summary`. Each test:
- `.nodeid` — test path (`tests/test_foo.py::test_bar`)
- `.outcome` — `"passed"` | `"failed"` | `"error"` | `"skipped"`
- `.call.longrepr` — failure traceback/message
- `.duration` — seconds

## Step 1: Detect log type and run overview

Detect by file extension and first bytes: JSONL (lines of `{`) vs JSON (starts with `{` containing `.tests`).

Run these in parallel:

**For app logs (JSONL):**

```bash
# 1a. Level distribution
jq -r '.l' FILE | sort | uniq -c | sort -rn

# 1b. Time span
jq -r '.t' FILE | sed -n '1p;$p'

# 1c. Line count
wc -l FILE

# 1d. Top clarinet modules by volume (exclude third-party noise)
jq -r 'select(.mod | startswith("clarinet.")) | .mod' FILE | sort | uniq -c | sort -rn | head -15
```

**For test reports (JSON):**

```bash
# 1a. Summary
jq '.summary' FILE

# 1b. Failed test names
jq -r '.tests[] | select(.outcome == "failed") | .nodeid' FILE

# 1c. Error categories
jq -r '.tests[] | select(.outcome == "failed") | .call.longrepr' FILE | grep -oP '(IntegrityError|ValidationError|AssertionError|TypeError|KeyError|HTTPException|ConnectionError|TimeoutError|NOT NULL|UNIQUE|500 Internal|404 Not Found|AcceptedNegativeData|UndefinedStatusCode|RejectedPositiveData)' | sort | uniq -c | sort -rn
```

Present the overview to the user as a concise summary table.

## Step 2: Error and warning triage (app logs only)

```bash
# 2a. Errors (streaming, no slurp)
jq -c 'select(.l == "ERROR") | {t, mod, fn, msg}' FILE

# 2b. Unique error patterns
jq -c 'select(.l == "ERROR") | "\(.mod):\(.fn) — \(.msg)"' FILE | sort | uniq -c | sort -rn

# 2c. Warnings by module (EXCLUDE known noise: pydicom, PIL, asyncio debug)
jq -c 'select(.l == "WARNING" and ((.mod // "") | test("^(pydicom|PIL|asyncio)") | not)) | "\(.mod):\(.fn)"' FILE | sort | uniq -c | sort -rn | head -20

# 2d. Exceptions with tracebacks
jq -c 'select(.exc != null) | {t, mod, fn, msg, exc: (.exc | split("\n") | last)}' FILE
```

## Step 3: Drill-down (based on findings or user focus)

Pick the relevant recipes. ALWAYS use streaming jq (never `-s` on large files).

### By module
```bash
jq -c 'select(.mod == "MODULE") | {t, l, fn, msg}' FILE | head -50
```

### By time window
```bash
jq -c 'select(.t >= "START" and .t <= "END") | {t, l, mod, fn, msg}' FILE
```

### By function
```bash
jq -c 'select(.fn == "FUNC") | {t, l, msg}' FILE
```

### By message pattern
```bash
jq -c 'select(.msg | test("PATTERN"; "i")) | {t, l, mod, fn, msg}' FILE | head -30
```

### Entity tracking (token, record ID, series UID)
```bash
# Find unique entities
jq -r '.msg' FILE | grep -oP 'ENTITY_PATTERN' | sort -u

# Follow entity through logs
jq -c 'select(.msg | contains("ENTITY_ID")) | {t, l, mod, fn, msg}' FILE
```

### Auth flow analysis
```bash
# Token lifecycle: write → read (cache hit/miss) → warning (expired/invalid)
jq -c 'select(.mod == "clarinet.api.auth_config") | {t, l, fn, msg}' FILE | head -30

# Unique tokens
jq -r 'select(.mod == "clarinet.api.auth_config" and .fn == "read_token") | .msg' FILE | grep -oP 'Token [a-f0-9]{8}' | sort | uniq -c | sort -rn
```

## Step 4: Produce the report

Structure:

### Log overview
- File, time span, total entries, level breakdown

### Issues found

For each issue (grouped by severity):
- **What**: error/warning pattern with count
- **Where**: module, function, first/last occurrence
- **Context**: surrounding log entries (time window ±2s around first occurrence)
- **Impact**: frequency, affected endpoints/records

### Noise / expected
- pydicom warnings (always present during DICOM operations, usually harmless)
- Routine cache hits/misses (DEBUG level)
- Any other high-volume low-signal patterns

### Recommendations
- Code changes to fix or silence issues
- Missing error handling to add
- Monitoring/alerting suggestions

## Rules

- NEVER use `jq -s` (slurp) on files > 1000 lines — use streaming `jq -c` piped to `sort | uniq -c`
- ALWAYS exclude pydicom/PIL/asyncio noise in warning analysis unless the user specifically asks about them
- For time-window queries: use ISO string comparison (works for lexicographic sort of ISO 8601)
- Prefer `jq -r` for human-readable output, `jq -c` for piping to sort/uniq
- Run independent jq queries in parallel (multiple Bash calls in one message)
- Read the overview results before deciding which drill-downs to pursue — don't run all recipes blindly
- For test reports: read `.call.longrepr` for SPECIFIC failed tests, not all at once (can be huge)
