# Proposal: unify-project-scaffold

## Why

`clarinet init --template` is broken for every pip-installed clarinet (#472): the payload lives in top-level `examples/`, which no wheel ships — verified against v0.10.20, which carries 0 `examples/` entries. It works only from a source checkout, so the audience it exists to serve cannot use it.

Underneath the packaging bug sits a duplicated delivery path: the template ships its own copy of the agent docs that `clarinet agent init` already delivers properly (namespaced, link-resolved, refreshable). The twins have drifted bidirectionally, and #485's `unique_by` documentation landed only in the copy that cannot be delivered — so it reaches no downstream user today.

Fixing packaging alone would preserve the duplication and keep two `init` paths that disagree about whether a project uses `tasks/` or `plan/`. Consolidating to one scaffold removes both problems at once.

## What Changes

**Template payload location**
- From: `examples/project_template/` at repo top level, resolved via `Path(clarinet.__file__).parent.parent / "examples"`
- To: `clarinet/scaffold/` inside the package, resolved via `Path(clarinet.__file__).parent`, force-shipped through `[tool.hatch.build] artifacts` — mirroring the mechanism `clarinet/docs` already uses successfully
- Reason: only files under `clarinet/` reach the wheel
- Impact: fixes #472; breaking for nobody (the current path never worked outside a checkout)

**`clarinet init` interface**
- From: bare `init` (emits `tasks/`, `static/`, `data/` + an inline heredoc `settings.toml`) plus `--template demo|research` plus `--list-templates`
- To: a single `clarinet init [path]` that always produces the full project skeleton
- Reason: two code paths disagreeing about project layout is what produced the `tasks/` vs `plan/` divergence; one template does not justify a template system
- Impact: **breaking** — `--template` and `--list-templates` are removed; `clarinet/cli/templates.py` is deleted

**Agent docs in a scaffolded project**
- From: the template ships its own `.claude/CLAUDE.md` + `.claude/rules/*.md`, with literal unsubstituted `<clarinet>/…` links and no refresh path
- To: `init` calls the existing `scaffold_agent_docs()`; the template's `.claude/` tree is deleted
- Reason: one authority per payload; the scaffold copy was strictly worse on every axis and had already drifted
- Impact: scaffolded projects get namespaced, link-resolved, `agent update`-refreshable docs

**`overview.md` classification**
- From: a managed doc written to `.claude/rules/clarinet/overview.md` with a "do not edit" header, while its own body instructs the user to replace its contents — and `agent update` overwrites whatever they write
- To: a **seed** written once to `<project>/.claude/CLAUDE.md`, with no managed header, never touched by `update`
- Reason: the file is project-owned by intent; the current wiring destroys user work
- Impact: fixes a live bug for existing projects too, not only newly scaffolded ones

**`agent update` orphan handling**
- From: managed files dropped from the payload linger in `.claude/rules/clarinet/` forever
- To: `update` prunes managed files no longer present in the payload
- Reason: without it, every already-scaffolded project keeps a stale `overview.md` after the seed split
- Impact: non-breaking; only affects framework-managed files carrying the managed header

**`config_tasks_path` default**
- From: `"./tasks/"`
- To: `"./plan/"`
- Reason: every doc, rule, and current downstream project uses `plan/`; the stale default is a silent trap for anyone who omits the setting
- Impact: **breaking** — projects on the default with a real `tasks/` dir fail at startup (known: `clarinet_carcinomatosis`, `examples/demo`). One-line fix; resolution failure carries an upgrade hint naming the flip

**Doc harvest (prerequisite, not optional)**
- Backport `{parent_id}`, `allow_path_collision`, and `unique_by` (+ "per level context" wording) from the template twin into `clarinet/docs/agent/claude/definitions.md` **before** deleting the template `.claude/` tree
- Reason: that copy is currently the only downstream-facing home for this content

## Capabilities

### New Capabilities

- `project-scaffolding`: what `clarinet init` produces and where the payload comes from — a single scaffold path sourced from a package-internal payload, dotfile-safe storage (`gitignore`/`env.example` written as dotted names at scaffold time), and the resulting project layout.
- `agent-doc-delivery`: the managed-vs-seed contract of `clarinet agent init|update` — which payload files are framework-owned and refreshed, which are seeded once and owned by the project, and how files dropped from the payload are pruned.
- `plan-directory-convention`: `config_tasks_path` defaults to `./plan/`, and an unresolvable plan root reports an upgrade-aware diagnostic.

### Modified Capabilities

None — the only existing capability in the spec store is `slicer-overlap-detection`, which this change does not touch.

## Impact

- **Code**: `clarinet/cli/main.py` (`init_project`, argparse), `clarinet/cli/templates.py` (**deleted**), `clarinet/utils/agent_scaffold.py` (managed/seed split, prune), `clarinet/settings.py` (default flip), plan-root resolution error path.
- **Package payload**: new `clarinet/scaffold/**` (moved from `examples/project_template/`, minus `.claude/`); `pyproject.toml` `artifacts` entry; version bump to **0.11.0** (breaking).
- **Repo**: `examples/project_template/.claude/` deleted; `examples/demo/tasks/` renamed to `plan/` (26 tracked files) with `recordflow_paths` updated.
- **Docs**: `clarinet/docs/agent/claude/definitions.md` (harvest); `clarinet/docs/project-setup.md` **and** `.claude/rules/project-setup.md` must change byte-identically (`project-setup` is in `DEEP_DOCS`, guarded by `test_deep_docs_identical_to_rules_seeds`); `README.md` lines 129/139; `CHANGELOG.md` breaking entry with migration line.
- **Tests**: `test_project_template_ships_same_section_rules` deleted; new guards for package-internal payload resolution, payload manifest completeness, `init` output shape, seed survival across `update`, and orphan pruning.
- **Downstream**: projects relying on the `./tasks/` default must set it explicitly or rename to `plan/`. No DB migration; no API change.
