---
description: Analyze Clarinet app logs (JSONL) or test reports (JSON) with jq
argument-hint: <path to log file> [focus: module, time range, error, entity]
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

## Step 0: Prepare files

When given a directory (not a single file):

1. **Decompress** archives (`.zip`, `.gz`, `.zst`) into a temp subdirectory
2. **Inventory** all log files — list each with line count, parseable lines, size
3. **Group** by origin: `clarinet.log*` → app, `clarinet_worker*` → worker, `*test-report*` → test
4. **NEVER concatenate** files into a single stream — always process per-file via `for f in FILES`

Present the inventory table to the user before proceeding.

## jq on JSONL — safe invocation pattern

**Known issue:** pynetdicom debug logs contain `\r\n` inside `msg` values. The literal newline splits one JSON object across two lines, breaking jq's parser. jq stops at the first parse error and silently drops the rest of the stream.

**Mandatory wrapper** — use `grep '^{' | jq` instead of bare `jq` for ALL JSONL queries:

```bash
# WRONG — jq stops at first broken line, 2>/dev/null hides the data loss:
jq -r '.l' FILE 2>/dev/null | sort | uniq -c

# RIGHT — pre-filter to valid JSON lines:
grep '^{' FILE | jq -r '.l' | sort | uniq -c
```

**After the overview step**, run a sanity check on each file:

```bash
for f in FILES; do
  total=$(wc -l < "$f")
  parsed=$(grep -c '^{' "$f")
  if [ "$parsed" -lt "$((total * 95 / 100))" ]; then
    echo "⚠ $(basename "$f"): $parsed/$total parseable ($(( (total-parsed)*100/total ))% lost)"
  fi
done
```

Report any data loss warnings to the user.

## Step 1: Detect log type and run overview

Detect by file extension and first bytes: JSONL (lines of `{`) vs JSON (starts with `{` containing `.tests`).

**Process each file individually.** Merge results across files of the same group.

Run these in parallel:

**For app logs (JSONL):**

```bash
# 1a. Level distribution (across all files in group)
for f in FILES; do grep '^{' "$f" | jq -r '.l'; done | sort | uniq -c | sort -rn

# 1b. Time span (first file's first line, last file's last line)
grep '^{' FIRST_FILE | head -1 | jq -r '.t'
grep '^{' LAST_FILE | tail -1 | jq -r '.t'

# 1c. Total line count + parseable count
for f in FILES; do
  total=$(wc -l < "$f"); parsed=$(grep -c '^{' "$f")
  echo "$total $parsed $(basename "$f")"
done

# 1d. Top clarinet modules by volume
for f in FILES; do grep '^{' "$f" | jq -r 'select((.mod // "") | startswith("clarinet.")) | .mod'; done | sort | uniq -c | sort -rn | head -15
```

**For test reports (JSON):**

```bash
# 1a. Summary
jq '.summary' FILE

# 1b. Failed test names
jq -r '.tests[] | select(.outcome == "failed" or .outcome == "error") | .nodeid' FILE

# 1c. Error categories
jq -r '.tests[] | select(.outcome == "failed" or .outcome == "error") | .call.longrepr' FILE | grep -oP '(IntegrityError|ValidationError|AssertionError|TypeError|KeyError|HTTPException|ConnectionError|TimeoutError|NOT NULL|UNIQUE|500 Internal|404 Not Found|AcceptedNegativeData|UndefinedStatusCode|RejectedPositiveData)' | sort | uniq -c | sort -rn
```

Present the overview to the user as a concise summary table. **Include data loss warnings** if any file had <95% parseable lines.

## Step 2: Error and warning triage (app logs only)

```bash
# 2a. Errors (streaming, across all files in group)
for f in FILES; do grep '^{' "$f" | jq -c 'select(.l == "ERROR") | {t, mod, fn, msg}'; done

# 2b. Unique error patterns
for f in FILES; do grep '^{' "$f" | jq -c 'select(.l == "ERROR") | "\(.mod):\(.fn) — \(.msg)"'; done | sort | uniq -c | sort -rn

# 2c. Warnings by module (EXCLUDE known noise: pydicom, PIL, asyncio debug)
for f in FILES; do grep '^{' "$f" | jq -c 'select(.l == "WARNING" and ((.mod // "") | test("^(pydicom|PIL|asyncio)") | not)) | "\(.mod):\(.fn)"'; done | sort | uniq -c | sort -rn | head -20

# 2d. Exceptions with tracebacks
for f in FILES; do grep '^{' "$f" | jq -c 'select(.exc != null) | {t, mod, fn, msg, exc: (.exc | split("\n") | last)}'; done
```

## Step 3: Drill-down (based on findings or user focus)

Pick the relevant recipes. ALWAYS use `grep '^{' | jq` (never bare `jq` on JSONL).

### By module
```bash
for f in FILES; do grep '^{' "$f" | jq -c 'select(.mod == "MODULE") | {t, l, fn, msg}'; done | head -50
```

### By time window
```bash
for f in FILES; do grep '^{' "$f" | jq -c 'select(.t >= "START" and .t <= "END") | {t, l, mod, fn, msg}'; done
```

### By function
```bash
for f in FILES; do grep '^{' "$f" | jq -c 'select(.fn == "FUNC") | {t, l, msg}'; done
```

### By message pattern
```bash
for f in FILES; do grep '^{' "$f" | jq -c 'select(.msg | test("PATTERN"; "i")) | {t, l, mod, fn, msg}'; done | head -30
```

### Entity tracking (token, record ID, series UID)
```bash
# Find unique entities
for f in FILES; do grep '^{' "$f" | jq -r '.msg'; done | grep -oP 'ENTITY_PATTERN' | sort -u

# Follow entity through logs
for f in FILES; do grep '^{' "$f" | jq -c 'select(.msg | contains("ENTITY_ID")) | {t, l, mod, fn, msg}'; done
```

### Auth flow analysis
```bash
# Token lifecycle: write → read (cache hit/miss) → warning (expired/invalid)
for f in FILES; do grep '^{' "$f" | jq -c 'select(.mod == "clarinet.api.auth_config") | {t, l, fn, msg}'; done | head -30

# Unique tokens
for f in FILES; do grep '^{' "$f" | jq -r 'select(.mod == "clarinet.api.auth_config" and .fn == "read_token") | .msg'; done | grep -oP 'Token [a-f0-9]{8}' | sort | uniq -c | sort -rn
```

## Step 4: Produce the report

Structure:

### Log overview
- File, time span, total entries, level breakdown
- **Data quality**: parseable line ratio per file, warnings if <95%

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
- NEVER concatenate multiple log files into one stream — process each file individually via `for f in FILES`
- NEVER use bare `jq` on JSONL — always `grep '^{' FILE | jq` to skip broken lines (pynetdicom \r\n split)
- ALWAYS run a parsed-vs-total sanity check after the overview step and report data loss
- ALWAYS exclude pydicom/PIL/asyncio noise in warning analysis unless the user specifically asks about them
- For time-window queries: use ISO string comparison (works for lexicographic sort of ISO 8601)
- Prefer `jq -r` for human-readable output, `jq -c` for piping to sort/uniq
- Run independent jq queries in parallel (multiple Bash calls in one message)
- Read the overview results before deciding which drill-downs to pursue — don't run all recipes blindly
- For test reports: read `.call.longrepr` for SPECIFIC failed tests, not all at once (can be huge)
