# Tasks: add-scripting-frame

## 1. Foundations

- [ ] 1.1 Add `typer` to `[project.dependencies]` in `pyproject.toml` (base package, no extras; pick the lower-bound pin uv resolves cleanly), run `uv sync`, and confirm `make typecheck` passes without a mypy override (typer ships `py.typed`)
- [ ] 1.2 Create `clarinet/scripting.py` with `Tally` (named counters, failure list, stdout summary rendering) — unit tests for counting, failure recording, and summary content

## 2. ScriptCtx

- [ ] 2.1 `ScriptCtx` core: `commit`, `limit`, `hit_limit(count)` (True iff limit set and count >= limit; frame never applies the limit itself) — unit tests for unset/set/boundary cases
- [ ] 2.2 `ctx.would(msg)`: uniform `[dry-run] would …` line via `typer.echo` to stdout — test asserts prefix and channel
- [ ] 2.3 `ctx.confirm(msg)`: True without prompting under `--yes`; interactive prompt on a TTY; refuses (False, no blocking) when stdin is not a TTY and `--yes` absent — tests for all three paths
- [ ] 2.4 Lazy cached `ctx.client`: built on first access from `settings.effective_api_base_url` / `effective_service_token` / `api_verify_ssl`, `--api-base` overrides the base URL; never constructed if untouched — tests via monkeypatched settings and a construction spy

## 3. @script decorator

- [ ] 3.1 Signature synthesis: strip leading `ctx: ScriptCtx` param, expose remaining params as typer CLI parameters, append standard options (`--commit`, `--limit`, `--yes`, `--api-base`); raise at decoration time on a name collision with a standard option — tests for custom-param passthrough and collision error
- [ ] 3.2 Async bridge and entry points: `asyncio.run` around the async body; decorated object directly callable for `__main__`; underlying typer app exposed for `CliRunner` — test runs a sample framed script in-process
- [ ] 3.3 Post-body wiring: tally summary echoed to stdout; rerun-with-`--commit` hint only in dry-run; exit 0 clean / 1 on recorded failures; uncaught exceptions propagate with non-zero exit — CliRunner tests for each exit path
- [ ] 3.4 `--help` contract: docstring becomes command description; all standard + custom options listed; no token-bearing option — CliRunner `--help` test

## 4. Documentation

- [ ] 4.1 Agent-doc page for downstream script authors (frame contract, target script shape, standard options, output/exit conventions, frame-only scope) at `clarinet/docs/agent/claude/scripting.md` with `paths: ["scripts/**"]` frontmatter; add `"scripting"` to `SECTION_RULES` in `tests/test_agent_scaffold.py` so the payload guards cover it; one-line pointer in `overview.md`

## 5. Verification

- [ ] 5.1 Full pass: `make check` and unit test suite green; CliRunner end-to-end of a realistic sample script (custom option + tally + dry-run and commit invocations) as the acceptance test
