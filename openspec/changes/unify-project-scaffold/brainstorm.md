<!--
Raw capture of the brainstorming exploration for unify-project-scaffold.

Source: /opsx:explore session on issue #472 (2026-07-26/27). The exploration ran
conversationally — codebase investigation, empirical wheel inspection, then a
decision chain resolved with the user. This file captures that as-is.

design.md reorganizes this into structured sections; the two are complementary,
not duplicates.
-->

# Brainstorm — unify-project-scaffold

## Background

Issue #472: `clarinet init --template <name>` is broken for any pip-installed
clarinet.

`clarinet/cli/templates.py:34-38` resolves the template source from **outside**
the package:

```python
def find_examples_dir() -> Path:
    import clarinet
    return Path(clarinet.__file__).parent.parent / "examples"
```

The wheel ships `packages = ["clarinet"]` plus two force-included subtrees
(`clarinet/static/**/*`, `clarinet/docs/**/*`). `examples/` is top-level, so it
is in neither → absent from the wheel. `copy_template()` hits `sys.exit(1)` for
every template, all of which `--list-templates` advertises. Works only from a
source checkout, which is why it went unnoticed.

## Investigation — what was verified, not assumed

Inspected the actual published artifacts rather than reasoning from config:

| Artifact | Entries | `clarinet/docs/**` | `examples/**` |
|---|---|---|---|
| `clarinet-0.10.20-py3-none-any.whl` (current release) | 335 | **14** ✅ | **0** ❌ |
| `clarinet-0.10.0-py3-none-any.whl` (local `dist/`) | 297 | 0 (predates #416) | **0** ❌ |
| `clarinet-0.10.0.tar.gz` (sdist) | — | — | 44 ✅ |

Wheel top-level is exactly `['clarinet', 'clarinet-0.10.20.dist-info']`, so
`Path(clarinet.__file__).parent.parent` is `site-packages` and
`site-packages/examples` does not exist.

The sdist *does* carry `examples/`, but that rescues nothing: pip builds the
wheel from the sdist, and the wheel is where the files vanish.

Side observation: the sdist also ships `examples/demo/.claude/settings.local.json`
— `_IGNORE_PATTERNS` scrubs at copy time, not at build time.

## Reframe — the bug is a symptom of a duplicated delivery path

The template is not merely a broken file copy. It is a **second, unmanaged
delivery mechanism for the agent docs**, competing with `clarinet agent init`:

| Axis | `clarinet agent init` | `init --template research` |
|---|---|---|
| Ships in wheel | ✅ via `artifacts` | ❌ |
| Deep-doc links | `{{CLARINET_DOCS}}` → resolved absolute path | `<clarinet>/clarinet/.claude/rules/…` — literal, never substituted |
| Destination | `.claude/rules/clarinet/` (namespaced) | `.claude/rules/` (bare, collides with the project's own) |
| Managed header | ✅ | ❌ |
| Refresh on upgrade | ✅ `agent update` | ❌ never |
| Test guard | content, token, link-resolution | **filename presence only** |

`test_no_unresolved_clarinet_repo_links` (`tests/test_agent_scaffold.py:66`)
explicitly forbids the literal `<clarinet>` in the packaged copy — and the
template copy is full of it.

## Drift evidence — bidirectional, with a smoking gun

`test_project_template_ships_same_section_rules` checks only that filenames
exist. Its own docstring concedes: *"Content parity is not covered here — the
twins differ by their deep-doc link form."* That one legitimate difference is
the crack all the drift entered through.

- **PR #485** (`unique_by`) updated `examples/project_template/.claude/rules/definitions.md`
  and **not** `clarinet/docs/agent/claude/definitions.md`.
- **PR #448** (`x-options.source` validation) updated the **packaged**
  `schemas.md` and not the template twin.

Each author updated whichever twin they happened to notice.

**Consequence:** the shipping `definitions.md` still says
*"`min_records`, `max_records` — per parent"* and documents no `unique_by`, no
`{parent_id}`, no `allow_path_collision`. `unique_by` appears in
`clarinet/docs/file-registry.md` and `recordflow-dsl.md`, but **not** in the
section doc an agent actually reads when writing `record_types.py`. The
`unique_by` documentation from #485 currently reaches no downstream user at all
— it lives only in the copy that cannot be delivered.

## Finding — `overview.md` is mis-classified

The packaged `overview.md` is written as a **project-owned** `CLAUDE.md`:

> *"This document is always loaded into the agent's context…"*
> *"**Replace this file's contents with your own study** (name, description, specifics)."*

But `scaffold_agent_docs` writes it to `.claude/rules/clarinet/overview.md`
stamped `<!-- managed by clarinet v… — do not edit; run 'clarinet agent update' -->`,
and `agent update` rewrites every `*.md` in that dir.

So the file tells the user to replace it, the header forbids it, and the updater
destroys their work if they do. Today the template's `.claude/CLAUDE.md` quietly
served this role correctly — project-owned, right location, never clobbered.
Deleting it without replacing the mechanism hands the clobber bug to every
project.

Content direction is favourable: `overview.md` is *ahead* of the template twin
(namespaced paths, `scripting.md` refs, resolved token links). Nothing to
harvest there.

## Finding — a named casualty for the default flip

Downstream survey of `config_tasks_path`:

| Project | Setting | Dir on disk |
|---|---|---|
| `clarinet_nir_liver` | `./plan/` | `plan/` |
| `clarinet_lymphoma_muscles` | `./plan/` | `plan/` |
| `clarinet_epigastric_segmentation` | `./plan/` | `plan/` |
| **`clarinet_carcinomatosis`** | **commented out → default** | **`tasks/`** ⚠️ |
| `clarinet-feature` | not set → default | `tasks/` |
| `examples/demo` | not set → default | `tasks/` |

Three projects are already explicitly on `plan/`. `clarinet_carcinomatosis` runs
on the `./tasks/` default with a real `tasks/` dir; so does the demo.

## Finding — the payload `.gitignore` trap

Verified: **hatchling does ship dotfiles** — `clarinet/ohif/.gitignore` is
present in the v0.10.20 wheel. But that file contains:

```
*
!.gitignore
!app-config.js
```

i.e. a `.gitignore` *inside the package* actively deciding what git tracks, and
therefore what hatchling ships. Dropping the template's `.gitignore` (`data/`,
`*.db`, `*.log`, `__pycache__/`) verbatim into the new package payload points
that same gun at the scaffold. Nothing matches today; the day someone adds a
`plan/data/` example it silently leaves git, then silently leaves the wheel —
issue #472 reintroduced one level down.

Repo `.gitignore` is a **denylist** (not an allowlist). `clarinet/static/` is
gitignored — which is why it needs an `artifacts` entry. `clarinet/docs` is not
gitignored, so its `artifacts` entry is belt-and-suspenders.

## Second incoherence found in the same command

Bare `clarinet init` creates `tasks/`, `static/`, `data/` (`cli/main.py:43-47`)
— the old convention, matching the still-current `config_tasks_path` default of
`"./tasks/"`. The research template uses `plan/`, which is what
`project-setup.md` documents. And `copy_template` mkdirs
`("tasks", "static", "data")` *after* copytree, so `init --template research`
produces a project with `plan/` **and** an orphan empty `tasks/`. `demo` uses
`tasks/`, `project_template` uses `plan/`. Two conventions, one repo.

## Decision chain

**Q1 — Is `--template demo` a runnable scaffold for pip users, or repo reading
material?**
→ **Reading material.** README already treats it that way
(`examples/demo/tasks/workflows/pipeline_flow.py` cited as an example to read).
It also hardcodes stand-specific RabbitMQ addresses, an admin password default,
and a PACS host — not a starting point to hand a pip user.

**Q2 — Should the research template carry `.claude/` at all?**
→ **No.** Every axis of the comparison table says `agent init` does it better.
Removing it dissolves the drift problem rather than packaging it.

**Q3 — Is `tasks/` deprecated?**
→ **Yes.**

**Q4 — Add a test?**
→ **Yes.**

**Q5 — Backport #485 to the packaged `definitions.md`?**
→ **Yes.**

**Q6 — How deep does the `tasks/` deprecation go?**
Options weighed: (a) scaffolds only — zero risk, trap survives; (b) flip the
default now — breaks `clarinet_carcinomatosis` and the demo, loud and one-line
fixable; (c) warn now, flip later.
→ **Flip the default now.** `config_tasks_path` default becomes `"./plan/"`.

**Q7 — With demo out of the CLI, `research` is the only template. Keep the
`--template` flag?**
→ **Retire it.** `clarinet init` always produces the project skeleton;
`--template` / `--list-templates` removed. This collapses the bare-vs-template
split that produced the `tasks/` vs `plan/` divergence in the first place.

**Q8 — One change or split into three?**
→ **One change, everything** — backport, packaging, seed split, `tasks/` flip,
tests, docs.

## Options matrix considered before Q2 resolved it

| | Approach | Cost | Kills the duplication? |
|---|---|---|---|
| 1 | Move payload to `clarinet/templates/` + `artifacts` | low | ❌ — two copies now *inside* the wheel |
| 2 | Force-include top-level `examples/` | low | ❌ — dumps a top-level `examples/` into site-packages, colliding with any other package doing the same |
| 3 | Fail loudly, fix docs + `--list-templates` | trivial | ❌ — `plan/` skeleton stays unreachable for pip users, the audience that needs it |
| **4** | **Split by mechanism** — template drops `.claude/`; `init` scaffolds the skeleton then calls `scaffold_agent_docs()` | medium | ✅ — one authority per payload |

Option 4 chosen. It also absorbs the layout incoherence (bare `init` produces the
same skeleton, orphan `tasks/` mkdir disappears) and shrinks what must be
packaged: strip `.claude/` and the research template is ~10 small files.

## Design trade-offs settled during exploration

**Harvest before delete.** The template `.claude/` is the only place `unique_by`,
`{parent_id}` and `allow_path_collision` exist in downstream-facing form.
Backport → then delete. Never the reverse. This is the single most important
ordering constraint in the change.

The harvest list is narrow — exactly **3 rows in `definitions.md`**. Everything
else is identical, link-form-only, or packaged-is-ahead.

**Payload dotfiles: rename rather than ship verbatim.** Store `.gitignore` as
`gitignore` and `.env.example` as `env.example` in the package; write the dotted
names at scaffold time. Two lines, and the live-gitignore trap is eliminated by
construction rather than by vigilance.

**`overview.md` becomes a seed, not a managed doc.** Write-once to
`<project>/.claude/CLAUDE.md`, no managed header, `update` never touches it. Put
this in `agent_scaffold` rather than in `init`, so existing projects — which have
the clobber bug right now — are fixed too.

## Defaults taken without asking (raised to the user, not objected to)

- **`agent update` prunes managed files no longer in the payload.** Otherwise
  every already-scaffolded project keeps a stale
  `.claude/rules/clarinet/overview.md` forever after the seed split.
- **Startup failure carries an upgrade hint naming the flip** when
  `config_tasks_path` cannot be resolved. Cheap, and without it the flip is a
  confusing failure for exactly the projects it breaks.

## Test strategy agreed

No wheel build in the test loop. Cheap proxies instead:

- Scaffold root resolves **inside** `Path(clarinet.__file__).parent` — fails
  today, passes after; this is the #472 regression guard.
- Payload manifest — every file the scaffold needs is present in the package
  dir. This is what catches the gitignore/`artifacts` silent-drop class.
- `init` into a tmp dir yields `plan/` + seed + managed rules and **no** `tasks/`.
- The seed survives `agent update`.
- `update` prunes orphans.

## Known gotcha for the docs step

`project-setup` is in `DEEP_DOCS`, so `test_deep_docs_identical_to_rules_seeds`
requires `clarinet/docs/project-setup.md` and `.claude/rules/project-setup.md`
to stay **byte-identical**. Editing only one fails the suite.

## Versioning

Breaking twice over (`config_tasks_path` default flip; `--template` /
`--list-templates` removal) → **0.11.0**.

## Optional pickup

`examples/demo/.claude/settings.local.json` currently ships in the sdist — a
per-developer file in a published artifact. Cheap to exclude while touching
packaging.
