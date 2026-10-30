<!--
Raw capture of superpowers:brainstorming output.

本檔原樣捕捉 brainstorming skill 的產出，不強制結構。
Skill 的自然產出通常是 decision log 格式（背景 → 決議鏈 Q1-Qn → 設計取捨），
但依對話內容可能有不同組織方式。

design.md 從本檔萃取並重新整理為結構化設計文件。

不要將本檔的內容複製到 design.md — design.md 是獨立的重組產物，
兩者互補但不重疊。
-->

# Brainstorm — `clarinet.scripting` typer frame for downstream operational scripts

Captured from the `/opsx:explore` session (2026-07-15). Design exploration ran as a
corpus analysis + fork-resolution dialogue; all forks resolved by the user.

## Background

Downstream projects (`clarinet_nir_liver`, `clarinet_lymphoma_muscles`) accumulate
operational one-shot scripts — backfills, repairs, remediations — that talk to the
Clarinet API via `ClarinetClient`. Each script hand-rolls the same frame: argparse
options (`--dry-run`/`--commit`/`--apply`, `--limit`, `--yes`, `--api-base`), client
construction, `asyncio.run` + exit-code wiring, counters/failed-list bookkeeping,
summary printing. User asked: build a little CLI framework with typer on top of
clarinet to minimize script code without violating DRY.

## Corpus analysis (ground truth)

16 scripts, 87–311 lines each, ~3,100 lines total (14 in nir_liver, 3 in lymphoma).

Anatomy of every script:

```
docstring (usage, warnings)                    ~10–40 lines
── FRAME (repeated everywhere) ─────────────────────────────
argparse block: --commit/--limit/--yes/...     ~15–40 lines
ClarinetClient construction + auth              ~5–8  lines
asyncio.run + SystemExit exit-code wiring       ~3–5  lines
counters dict / failed list / summary print    ~15–25 lines
"DRY-RUN: rerun with --commit" hint             ~2–3  lines
limit check inside the loop                     ~2–4  lines
── ALGORITHM (genuinely unique) ────────────────────────────
fetch → group → decide → act                   the real work
```

Frame ≈ 40–80 lines/script → ~800–1,000 lines of pure boilerplate. Worse than the
volume is the **drift** — three conventions for the same concepts:

| Concern | A | B | C |
|---|---|---|---|
| Write gate | `--commit` (default dry-run) — 11 scripts, all nir_liver | `--apply` (default dry-run) — 2 scripts | `--dry-run` (default **writes!**) — 2 lymphoma scripts |
| Auth | `settings.effective_api_base_url` + `effective_service_token` — 11 | `--api-base` + `CLARINET_ADMIN_PASSWORD` env — 3 lymphoma | — |
| Output | `print()` — nir_liver | `logger` — lymphoma | — |
| Exit code | `SystemExit(1 if failed else 0)` — some | none — others | — |

The lymphoma `--dry-run` convention is dangerous: forgetting the flag writes to prod
(violates the project's safe-defaults principle).

Variance that must be respected (over-abstraction risk):

- `--limit` means three different things across scripts: limit *checked*
  (repair_missing_nifti), limit *created* (backfill_second_reviews), limit
  *affected* (remediate_orientation_bug). Frame supplies the value; only the
  script knows where to apply it.
- Not every script has an API client (`screenshot_lesions.py` is pure
  Slicer/filesystem) → client must be lazy.
- Custom args stay custom: `--series`, positional xlsx, `--master/--out` Paths,
  mutually exclusive groups, interactive confirm with data-loss warnings.

## Approaches considered

- **A. argparse helpers in clarinet** (shared `add_common_args()`): zero new deps,
  fixes DRY on common flags, but custom per-script args stay verbose and help UX
  stays plain.
- **B. typer-based frame in `clarinet.scripting`** (chosen): `@script` decorator +
  `ScriptCtx`; custom args become annotated function params; polished `--help`;
  one new small stable dep (typer→click). DRY win concentrated where it matters.
- **C. Full project CLI** (central registry, `clarinet script run <name>` or
  `python -m scripts <name>`): discoverability, but breaks existing
  runbook/file-path habits and adds a registry to maintain — YAGNI now.

Recommendation was B; user confirmed (asked for typer explicitly in the prompt).

## Decision chain

- **Q1 — Retrofit the 16 existing scripts?** → **No migration.** New scripts use
  the frame; old ones convert only if touched again. Some (e.g.
  `conform_segs_to_grid.py`) must stay bit-stable for pending prod migrations.
- **Q2 — Output channel?** → **`typer.echo`/stdout for operator-facing lines**
  (tally summary, `[dry-run] would …` lines, hints); **clarinet logger for
  diagnostics**. The project's "no print, use logger" feedback governs app code,
  not CLI UX.
- **Q3 — Dependency placement?** → **typer as a hard dependency** of clarinet
  (not an extra). Downstreams install the full framework anyway. Migrating
  clarinet's own argparse `cli/main.py` to typer is explicitly out of scope.
- **Q4 — Absorption level?** → **Frame only.** Options, client, tally, exit code.
  No `iter_records` auto-counting magic, no loop absorption, no `BackfillScript`
  base class — script bodies share nothing.

## Additional locked decisions

- Standard write-gate convention: `--commit`, default False = dry-run (safe
  default; matches 11/16 existing scripts).
- Standard options injected by `@script`: `--commit`, `--limit N`, `--yes`,
  `--api-base` (override of settings). Service token comes from settings/env
  only — never a CLI flag (visible in `ps`/shell history).
- `ScriptCtx`: `.commit`, `.limit` + `.hit_limit(count)`, `.tally` (counters +
  failed list), `.confirm(msg)` honoring `--yes`, `.would(msg)` uniform dry-run
  lines, lazy `.client` built from `settings.effective_api_base_url` /
  `effective_service_token` / `api_verify_ssl` (env-overridable — unifies both
  existing auth styles for free).
- Async bridge owned by the decorator (typer is sync-only): `asyncio.run`, exit
  code 1 when `tally.failed` is non-empty.
- Scripts stay standalone files (`uv run python scripts/x.py`).
- Docs for downstream script authors: agent-doc page via the `clarinet agent`
  mechanism (`clarinet/docs/` + `.claude/rules/` golden-copy pair).
- Tests: typer `CliRunner` unit tests in the framework.

## Deferred to design

- Exact `Tally` API surface (count/fail methods, summary rendering).
- `@script` ↔ typer parameter-merging mechanics (signature synthesis).
- Whether `ScriptCtx` client cleanup is decorator-managed (`finally`) or
  script-managed (`async with ctx.client`).
