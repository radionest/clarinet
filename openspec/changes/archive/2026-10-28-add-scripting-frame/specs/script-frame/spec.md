# Delta Spec: script-frame

## ADDED Requirements

### Requirement: Standard script options
The `@script` decorator (public surface: `from clarinet.scripting import script, ScriptCtx`) SHALL build a single-command typer application that accepts the standard options `--commit` (bool, default False), `--limit` (int, default none), `--yes` (bool, default False), and `--api-base` (str, default none). The script's docstring SHALL become the command's `--help` description. The service token MUST NOT be accepted as a CLI option.

#### Scenario: Dry-run is the default
- **WHEN** a framed script is invoked without `--commit`
- **THEN** the body receives `ctx.commit == False`

#### Scenario: Help output
- **WHEN** a framed script is invoked with `--help`
- **THEN** the output lists `--commit`, `--limit`, `--yes`, and `--api-base` alongside the script's own options, with the docstring as the description, and offers no token-bearing option

### Requirement: Custom parameters merge into the CLI
Function parameters other than the leading `ctx: ScriptCtx` SHALL be exposed as typer CLI parameters (arguments/options per typer's usual inference and annotations), merged with the standard options. A custom parameter whose name collides with a standard option MUST raise at decoration time.

#### Scenario: Custom option accepted
- **WHEN** the decorated function declares `series: str | None = None`
- **THEN** the CLI accepts `--series <value>` and the body receives that value

#### Scenario: Name collision rejected early
- **WHEN** the decorated function declares a parameter named `commit`
- **THEN** decoration fails with an error naming the collision (before any CLI invocation)

### Requirement: Async bridge and entry points
The decorator SHALL accept an `async def` body and run it to completion via `asyncio.run`. The decorated object SHALL be directly callable as the script entry point (`if __name__ == "__main__": main()`) and SHALL expose the underlying typer app for in-process testing with `CliRunner`.

#### Scenario: Async body executes under CliRunner
- **WHEN** the decorated script is invoked through `CliRunner` against its exposed typer app
- **THEN** the async body runs to completion in-process and the runner captures its output and exit code

### Requirement: Limit supply with script-owned semantics
`ScriptCtx` SHALL expose the `--limit` value as `ctx.limit` and provide `ctx.hit_limit(count)` returning True iff a limit is set and `count >= limit`. The frame MUST NOT apply the limit to any iteration itself — what the count means is the script's decision.

#### Scenario: No limit set
- **WHEN** the script runs without `--limit`
- **THEN** `ctx.limit` is none and `ctx.hit_limit(n)` returns False for any `n`

#### Scenario: Limit reached
- **WHEN** the script runs with `--limit 2`
- **THEN** `ctx.hit_limit(2)` returns True and `ctx.hit_limit(1)` returns False

### Requirement: Tally, summary, and exit codes
`ScriptCtx` SHALL provide a tally with named counters and a failure list. After the body returns, the frame SHALL echo a summary of counters and failures to stdout, and exit with code 1 if any failures were recorded, else 0. In dry-run mode the summary SHALL include a rerun-with-`--commit` hint; with `--commit` it SHALL NOT. Exceptions escaping the body MUST NOT be swallowed by the frame and SHALL produce a non-zero exit.

#### Scenario: Clean run exits zero
- **WHEN** the body records counters but no failures
- **THEN** stdout ends with a summary of the counters and the process exits 0

#### Scenario: Recorded failures exit one
- **WHEN** the body records at least one failure in `ctx.tally`
- **THEN** the summary lists the failures and the process exits 1

#### Scenario: Dry-run hint
- **WHEN** a dry-run invocation completes
- **THEN** the summary includes a hint to rerun with `--commit`, and a `--commit` invocation of the same script does not print that hint

#### Scenario: Uncaught exception propagates
- **WHEN** the body raises an unhandled exception
- **THEN** the process exits non-zero and the exception is visible (not silently converted to a clean summary)

### Requirement: Uniform dry-run lines
`ctx.would(msg)` SHALL write a uniformly prefixed dry-run line (`[dry-run] would …`) to stdout.

#### Scenario: Would line format
- **WHEN** the body calls `ctx.would("invalidate record 7")`
- **THEN** stdout contains a single line combining the uniform dry-run prefix and the message

### Requirement: Confirmation gate honoring --yes
`ctx.confirm(msg)` SHALL return True without prompting when `--yes` was passed; SHALL prompt interactively on a TTY otherwise; and SHALL refuse (return False) without blocking when stdin is not a TTY and `--yes` was not passed.

#### Scenario: --yes skips the prompt
- **WHEN** the script runs with `--yes` and the body calls `ctx.confirm("proceed?")`
- **THEN** it returns True with no prompt written

#### Scenario: Non-interactive without --yes refuses
- **WHEN** stdin is not a TTY, `--yes` was not passed, and the body calls `ctx.confirm("proceed?")`
- **THEN** it returns False without hanging on input

### Requirement: Lazy settings-based client
`ctx.client` SHALL lazily construct and cache a `ClarinetClient` on first access, configured from `settings.effective_api_base_url`, `settings.effective_service_token`, and `settings.api_verify_ssl`; a `--api-base` value SHALL override the base URL. A script that never touches `ctx.client` MUST NOT construct a client.

#### Scenario: No client for filesystem-only scripts
- **WHEN** a framed script that never accesses `ctx.client` runs without any API configuration
- **THEN** it completes without constructing a `ClarinetClient`

#### Scenario: api-base override
- **WHEN** the script runs with `--api-base http://example:9999`
- **THEN** `ctx.client` is constructed with that base URL instead of the settings value

### Requirement: Downstream agent documentation
The frame SHALL ship a documentation page for downstream script authors in the packaged agent-docs payload (`clarinet/docs/agent/claude/`), auto-installed into downstream projects by `clarinet agent init`/`update`. The page SHALL carry `paths:` frontmatter scoping it to the project-root `scripts/` directory and SHALL satisfy the payload guard tests (file presence, resolvable `{{CLARINET_DOCS}}` links if any, no Cyrillic text).

#### Scenario: Page ships with the payload
- **WHEN** the agent-docs payload tests run
- **THEN** the scripting page is present in `clarinet/docs/agent/claude/` and passes the payload guards

#### Scenario: Installed downstream
- **WHEN** `clarinet agent init` runs in a downstream project
- **THEN** the scripting documentation page is installed into `.claude/rules/clarinet/` with the managed header and its frontmatter preserved
