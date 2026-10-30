# Proposal: add-scripting-frame

## Why

Downstream projects (`clarinet_nir_liver`, `clarinet_lymphoma_muscles`) keep writing operational one-shot scripts — backfills, repairs, remediations. Sixteen such scripts (~3,100 lines) each hand-roll the same ~40–80-line frame: argparse options, `ClarinetClient` construction, `asyncio.run` wiring, counters, summary printing. Worse than the ~1,000 duplicated lines is the drift: three competing write-gate conventions coexist, one of which (`--dry-run`, default **writes**) violates the safe-defaults principle — forgetting a flag mutates production. New scripts keep being written; the framework should ship the frame once.

## What Changes

- New `clarinet.scripting` module: a `@script` decorator (single-command typer app with standard options, async bridge, tally summary, exit code) plus a `ScriptCtx` handed to the script body (write gate, limit, tally, confirm, lazy `ClarinetClient`).
- `typer` becomes a hard dependency of clarinet.
- New agent-doc page for downstream script authors, shipped via the existing `clarinet agent init/update` mechanism.

**Write-gate convention (for scripts adopting the frame)**
- From: three conventions across downstream scripts — `--commit` (default dry-run), `--apply` (default dry-run), `--dry-run` (default writes)
- To: one convention — `--commit`, default `False` = dry-run
- Reason: safe default; an operator moving between projects can trust muscle memory
- Impact: non-breaking — existing scripts are not retrofitted; the convention applies to new scripts

**Client auth (for scripts adopting the frame)**
- From: two styles — settings-based (`effective_api_base_url` + `effective_service_token`) vs env/flag-based (`--api-base` + `CLARINET_ADMIN_PASSWORD`)
- To: one lazy `ctx.client` built from `settings.effective_*` (already env-overridable), with an `--api-base` CLI override; service token never accepted as a CLI flag
- Reason: settings layer already unifies env and TOML; secrets must not appear in `ps`/shell history
- Impact: non-breaking, additive

## Capabilities

### New Capabilities

- `script-frame`: standard CLI frame for downstream operational scripts — `@script` decorator (standard options `--commit/--limit/--yes/--api-base`, asyncio bridge, tally summary, exit codes), `ScriptCtx` runtime surface, output conventions (typer.echo for operator lines, logger for diagnostics), and the downstream-facing agent documentation.

### Modified Capabilities

None — the spec store has no existing capabilities, and no existing clarinet behavior changes.

## Impact

- **Code**: new `clarinet/scripting.py` (framework module, ~200–300 lines); no changes to existing modules.
- **Dependencies**: `typer` added to `pyproject.toml` [project.dependencies] (pulls `click`; ships `py.typed`, so no mypy override needed).
- **Docs**: new page in the packaged agent-docs payload (`clarinet/docs/agent/claude/scripting.md`, auto-discovered by the scaffold glob); installed downstream via `clarinet agent init/update`; joins the payload guard tests in `tests/test_agent_scaffold.py`.
- **Tests**: unit tests via typer `CliRunner` in `tests/`.
- **Not affected**: server/API, DB schema (no migrations), existing downstream scripts (no retrofit), clarinet's own argparse CLI (`clarinet/cli/main.py` stays argparse).
