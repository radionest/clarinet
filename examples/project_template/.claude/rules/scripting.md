---
paths:
  - "scripts/**"
---

# Operational scripts (`scripts/`) — the `clarinet.scripting` frame

One-shot operational scripts — backfills, repairs, remediations — live in the
project-root `scripts/` directory and run as standalone CLI processes
(`uv run python scripts/<name>.py`). Do not confuse them with `plan/scripts/`
(Slicer scene scripts) — those follow their own rules.

Every new script here uses the framework's `clarinet.scripting` frame instead
of hand-rolling argparse, client construction, counters, and exit codes.

## Target shape

```python
"""One-line purpose. Docstring of the FUNCTION becomes --help."""

from clarinet.scripting import script, ScriptCtx


@script()
async def main(ctx: ScriptCtx, series: str | None = None) -> None:
    """Invalidate finished create-nifty records whose volume file is missing."""
    async with ctx.client as client:
        checked = 0
        async for record in client.iter_records(
            record_type_name="create-nifty", record_status="finished"
        ):
            if ctx.hit_limit(checked):
                break
            checked += 1
            ctx.tally.count("checked")
            if series and record.series_uid != series:
                continue
            if not ctx.commit:
                ctx.would(f"invalidate record {record.id}")
                continue
            try:
                await client.invalidate_record(record.id, mode="hard", reason="...")
                ctx.tally.count("invalidated")
            except Exception as exc:  # per-item resilience
                ctx.tally.fail(f"record {record.id}", str(exc))


if __name__ == "__main__":
    main()
```

Custom parameters are plain typer-style function parameters: no default →
positional argument, with default → `--option`. The first parameter is always
`ctx: ScriptCtx`.

## Standard options (injected by @script)

| Option | Default | Meaning |
|---|---|---|
| `--commit` | off | Actually write. **Default is always dry-run** — never invert this. |
| `--limit N` | none | Cap the run. The script decides what N counts (checked/created/affected) via `ctx.hit_limit(count)`. |
| `--yes` | off | Pre-approve `ctx.confirm(...)` prompts (for non-interactive runs). |
| `--api-base URL` | none | Override `settings.effective_api_base_url`. The service token is settings/env only — never a flag. The token is attached to whatever host this points at — only use trusted bases. |

## ScriptCtx API

| Member | Contract |
|---|---|
| `ctx.commit` | Write gate. Branch on it around every mutation. |
| `ctx.limit` / `ctx.hit_limit(count)` | The frame supplies the value; apply it yourself. |
| `ctx.tally.count(key, n=1)` / `ctx.tally[key]` | Named counters, echoed as the final summary. |
| `ctx.tally.fail(item, detail)` | Per-item failure; any failure ⇒ exit code 1. |
| `ctx.would(msg)` | Uniform `[dry-run] would …` stdout line. |
| `ctx.confirm(msg)` | Interactive gate; honors `--yes`; refuses on non-TTY without `--yes`. |
| `ctx.client` | Lazy `ClarinetClient` from settings; enter with `async with ctx.client as client:`. Never touched ⇒ never constructed. |

## Conventions

- Operator-facing lines (would/summary/hints) go to stdout (`typer.echo` inside
  the frame); use `from clarinet.utils.logger import logger` for diagnostics.
- Exit codes: 0 clean, 1 when `ctx.tally.fail(...)` was recorded; uncaught
  exceptions crash loudly on purpose. Wrap per-item work in try/except +
  `tally.fail` when one bad item must not abort the run.
- The frame is deliberately frame-only: it never iterates, retries, or decides
  limit semantics for you. Do not grow loop helpers onto it.
- Test scripts in-process: `from typer.testing import CliRunner;`
  `CliRunner().invoke(main.app, ["--limit", "2"])`.
