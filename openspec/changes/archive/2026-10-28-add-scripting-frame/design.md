# Design: add-scripting-frame

## Context

Downstream clarinet projects accumulate operational one-shot scripts (backfills, repairs, remediations) that talk to the API via `ClarinetClient`. A 16-script corpus across `clarinet_nir_liver` and `clarinet_lymphoma_muscles` shows every script is the same sandwich: a repeated ~40–80-line frame (options parsing, client construction, asyncio wiring, counters, summary, exit code) around a genuinely unique algorithm (fetch → group → decide → act). The frame has drifted into three write-gate conventions and two auth styles; one convention defaults to writing to production.

Constraints discovered in the corpus that the design must respect:

- `--limit` semantics vary by script (limit *checked* vs *created* vs *affected*) — only the script knows what the limit counts.
- Not every script uses the API (`screenshot_lesions.py` is Slicer/filesystem-only) — client construction must be lazy.
- Custom per-script arguments are common (`--series`, positional paths, mutually exclusive groups) and must stay first-class.
- Existing scripts are historical artifacts; some (e.g. `conform_segs_to_grid.py`) must stay bit-stable for pending prod migrations.

Stakeholders: downstream script authors/operators (primary), clarinet maintainers (own the module), agents writing scripts in downstream repos (consume the agent-doc page).

## Goals / Non-Goals

**Goals:**

- Ship the frame once, in the framework: standard options, client, tally, summary, exit codes.
- One write-gate convention with a safe default (dry-run unless `--commit`).
- Custom per-script args stay terse (typer-annotated function params).
- Scripts remain standalone files runnable as `uv run python scripts/x.py`.
- Frame is testable in clarinet (typer `CliRunner`).

**Non-Goals:**

- No retrofit of the 16 existing downstream scripts.
- No absorption of loop logic (no `iter_records` auto-counting, no `BackfillScript` base class, no per-item retry/progress machinery).
- No central script registry, no `clarinet script run <name>` subcommand, no `python -m` entry points.
- No migration of clarinet's own `cli/main.py` from argparse to typer.
- No new auth mechanisms — reuse `settings.effective_*`.

## Decisions

### D1: Placement — framework module `clarinet/scripting.py`

- **Choice**: single module file in the clarinet package, public surface `from clarinet.scripting import script, ScriptCtx`.
- **Rationale**: both downstream projects already depend on clarinet, which already ships the operator-facing `ClarinetClient`; a single file keeps KISS and can split later if it grows.
- **Alternatives considered**: downstream-copied template (drifts, the exact disease being cured); separate PyPI package (release overhead for ~250 lines).

### D2: typer as a hard dependency

- **Choice**: add `typer` to `[project.dependencies]`; base package only (no `rich` requirement).
- **Rationale**: user decision (explore Q3). Custom args as annotated params + polished `--help` is where typer beats argparse helpers; downstreams install the full framework anyway. `py.typed` ships with typer — no mypy override needed.
- **Alternatives considered**: optional extra `clarinet[scripts]` (import errors at script runtime are worse than one small install); argparse-only helpers (fixes DRY on standard flags but keeps custom args verbose — rejected in explore).

### D3: Write gate — `--commit`, default False

- **Choice**: every framed script is dry-run by default; `--commit` enables writes. `ScriptCtx.commit: bool`.
- **Rationale**: safe default (project principle: safety booleans default to safe mode); matches the majority convention (11/16 existing scripts).
- **Alternatives considered**: `--apply` (minority convention, same semantics — pick one name); `--dry-run` flag (default writes — rejected as unsafe).

### D4: Standard options set

- **Choice**: `--commit` (D3), `--limit N` (int, optional), `--yes` (skip confirmation), `--api-base URL` (override `settings.effective_api_base_url`). Service token is **never** a CLI flag — settings/env only.
- **Rationale**: these four appear across the corpus; token on the command line leaks via `ps` and shell history.
- **Alternatives considered**: `--verbose`/`--quiet` (YAGNI — logger config already env-driven); per-script standard `--series` (not universal; stays a custom param).

### D5: `ScriptCtx` runtime surface

- **Choice**: `ctx.commit`, `ctx.limit` + `ctx.hit_limit(count)` (frame supplies the value, script decides what it counts), `ctx.tally` (counters + failed list), `ctx.confirm(msg)` (honors `--yes`; refuses in non-TTY without `--yes`), `ctx.would(msg)` (uniform `[dry-run] would …` line), lazy cached `ctx.client` property.
- **Rationale**: covers every frame concern found in the corpus while leaving loop semantics with the script (limit variance, D8).
- **Alternatives considered**: passing plain function kwargs instead of a ctx object (signature noise, no place for tally/client); auto-counting iteration helpers (rejected — limit semantics vary).

### D6: Client lifecycle — script-managed `async with`

- **Choice**: `ctx.client` lazily constructs and caches a `ClarinetClient` from `settings.effective_api_base_url` / `effective_service_token` / `api_verify_ssl` (with `--api-base` override); the script enters it explicitly: `async with ctx.client as client:`.
- **Rationale**: mirrors how 11/16 scripts already use the client; keeps the decorator free of cleanup magic; laziness means Slicer/filesystem-only scripts never construct one.
- **Alternatives considered**: decorator-managed `finally: close()` (hidden lifecycle, surprises when a script wants two clients or none).

### D7: Async bridge and exit codes in the decorator

- **Choice**: `@script` wraps the user's `async def` in a sync callable via `asyncio.run` (typer is sync-only). After the body returns: print tally summary; print the `rerun with --commit` hint when in dry-run; exit 1 if `tally.failed` is non-empty, else 0. Uncaught exceptions propagate (traceback + non-zero) — no blanket catch.
- **Rationale**: every script currently hand-rolls this wiring, several inconsistently (some scripts always exit 0 even on failures).
- **Alternatives considered**: anyio/loop reuse (YAGNI — these are standalone processes that own their loop).

### D8: Frame-only absorption

- **Choice**: the module standardizes options, client, tally, output, exit codes — nothing about how the script iterates or decides.
- **Rationale**: corpus bodies share no structure; absorbing loops would force the three `--limit` meanings into one and break real scripts. User decision (explore Q4).
- **Alternatives considered**: template-method base class (over-engineering, YAGNI).

### D9: Output channels

- **Choice**: `typer.echo` (stdout) for operator-facing lines — tally summary, `ctx.would()` lines, hints, confirmations; `clarinet.utils.logger` for diagnostics (per-item errors, debug detail).
- **Rationale**: user decision (explore Q2). The project's "no print, use logger" feedback governs app code; a CLI's UX channel is stdout.
- **Alternatives considered**: logger-only (operator output entangled with log sinks/levels); rich tables (extra dep, YAGNI).

### D10: Decorator mechanics and testability

- **Choice**: `@script()` synthesizes the typer command signature from the user function's parameters (first param `ctx: ScriptCtx` is stripped and injected; standard options appended via `inspect.Signature` manipulation). The decorated object stays directly callable for `if __name__ == "__main__": main()` and exposes the underlying `typer.Typer` app (e.g. `main.app`) for `CliRunner` tests.
- **Rationale**: keeps the target script shape minimal (docstring → `--help`, params → options) while making the frame unit-testable in clarinet without subprocess spawns.
- **Alternatives considered**: requiring each script to build its own `typer.Typer()` and add the command (boilerplate returns); click-style decorator stacking for standard options (more verbose per script).

## Risks / Trade-offs

- **[Risk]** A script already running inside an event loop (e.g. embedded in Slicer) would crash on `asyncio.run` → Mitigation: document that framed scripts are standalone CLI processes; Slicer-embedded helpers are out of scope and keep their existing pattern.
- **[Risk]** Frame becomes an attractive nuisance — future loop/retry/progress helpers creep in → Mitigation: Non-Goals section here plus an explicit "frame-only" contract in the agent-doc page.
- **[Risk]** The new doc page rots or breaks payload invariants (unresolved tokens, non-English text) → Mitigation: it joins the existing payload guard tests in `tests/test_agent_scaffold.py` (presence, token-link resolution, no-Cyrillic).
- **[Risk]** typer/click version conflicts with downstream pins → Mitigation: loose lower-bound pin (resolve exact floor during implementation via uv); no `rich`/`shellingham` extras required.
- **[Trade-off]** A hard dep the server runtime never imports → accepted per user decision; typer+click are small and stable.
- **[Trade-off]** `--commit` breaks muscle memory of the two `--dry-run`-style lymphoma scripts → accepted; those scripts are not retrofitted and remain valid as-is.

## Migration Plan

N/A — additive library feature: no deployment, endpoint, or DB changes; no downstream migration required. Downstream projects adopt on their next clarinet version bump, for new scripts only. Rollback = not using the module.

### D11: Docs page placement — agent payload, scoped to root `scripts/`

- **Choice**: `clarinet/docs/agent/claude/scripting.md` with `paths: ["scripts/**"]` frontmatter — auto-discovered by the `scaffold_agent_docs` glob, installed downstream into `.claude/rules/clarinet/`, guarded by the payload tests. Added to `SECTION_RULES` in `tests/test_agent_scaffold.py`.
- **Rationale**: agent/claude pages are the downstream-facing set; unlike the top-level deep docs they have no byte-identical `.claude/rules/` twin. The page targets the project-root `scripts/` dir (operational one-shots) — deliberately distinct from `plan/scripts/` (Slicer scene scripts), which keeps its own page.
- **Alternatives considered**: top-level deep doc + `.claude/rules/` golden pair (wrong mechanism — that set documents the framework for clarinet's own repo, not downstream authors).

## Open Questions

- Minimum typer version pin — pick the floor that uv resolves cleanly against current deps during implementation (plan assumes `typer>=0.15`).
