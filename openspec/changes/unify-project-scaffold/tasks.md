# Tasks: unify-project-scaffold

## 1. Pre-flight and harvest (gates group 5)

- [x] 1.1 Resolve the open question: does the scaffold create a project-root `scripts/` dir? The packaged `overview.md` already documents it, but the `clarinet.scripting` frame belongs to the in-progress `add-scripting-frame` change. Decide and record in design.md
- [x] 1.2 Port the `{parent_id}` row into the path-pattern table of `clarinet/docs/agent/claude/definitions.md`
- [x] 1.3 Port the `allow_path_collision` row into the `FileRef` field table of the same file
- [x] 1.4 Replace `min_records, max_records — per parent` with the "per level context" wording and add the `unique_by` row
- [x] 1.5 Diff all 7 section rules plus `CLAUDE.md`/`overview.md` between `examples/project_template/.claude/` and `clarinet/docs/agent/claude/` to confirm nothing else in the template copy is ahead; record the result

## 2. Move the payload into the package

- [x] 2.1 `git mv` `examples/project_template/{settings.toml,settings.custom.toml,plan}` → `clarinet/scaffold/`
- [x] 2.2 `git mv` `examples/project_template/.env.example` → `clarinet/scaffold/env.example` and `.gitignore` → `clarinet/scaffold/gitignore` (undotted, per D6)
- [x] 2.3 Add `clarinet/scaffold/**/*` to `[tool.hatch.build] artifacts` in `pyproject.toml`
- [x] 2.4 Add a scaffold-root resolver returning `Path(clarinet.__file__).resolve().parent / "scaffold"`, raising a clear error when the payload is absent (mirror `agent_scaffold._package_docs_dir` / `agent_source_dir`)
- [x] 2.5 Confirm `git status` shows the moved payload as tracked and `git check-ignore` reports no payload file as ignored

## 3. Managed/seed split in `agent_scaffold`

- [x] 3.1 Partition the payload into a managed set (`definitions`, `workflows`, `anonymization`, `slicer`, `schemas`, `utils`, `scripting`) and a seed set (`overview.md` → `<project>/.claude/CLAUDE.md`)
- [x] 3.2 Write seed documents without the managed header, only when the target is absent; report preserved targets
- [x] 3.3 On `update`, prune files in the managed dir that carry the managed header but are no longer in the payload; leave unheadered files alone
- [x] 3.4 Special-case the pruned `overview.md`: move it to `<project>/.claude/CLAUDE.md` with the managed header stripped when that path is free; delete it when a project overview already exists

## 4. Rewire `clarinet init`

- [x] 4.1 Rewrite `init_project(path)` in `clarinet/cli/main.py` to copy the package scaffold payload, skipping files that already exist and reporting the skips
- [x] 4.2 Materialize `gitignore` → `.gitignore` and `env.example` → `.env.example` during the copy
- [x] 4.3 Call `scaffold_agent_docs("claude", project_dir=..., mode="init")` after the payload copy
- [x] 4.4 Delete the `("tasks", "static", "data")` mkdir loop and the inline heredoc `settings.toml`
- [x] 4.5 Remove `--template` and `--list-templates` from the argparse definition and drop the `template` parameter from `init_project`
- [x] 4.6 Point the completion output at `examples/demo/` in the repository as worked reading material

## 5. Remove the superseded delivery path

- [x] 5.1 Delete `clarinet/cli/templates.py`
- [x] 5.2 Delete `examples/project_template/.claude/` (only after group 1 lands — it is the sole home of the harvested content)
- [x] 5.3 Remove the now-empty `examples/project_template/` directory
- [x] 5.4 Delete `test_project_template_ships_same_section_rules` from `tests/test_agent_scaffold.py` along with the `PROJECT_TEMPLATE_*` constants it uses

## 6. Flip the `plan/` convention

- [x] 6.1 Change the `config_tasks_path` default in `clarinet/settings.py` from `"./tasks/"` to `"./plan/"`
- [x] 6.2 Convert the missing-root path from silent degradation to a hard failure: `load_python_config` (`clarinet/config/python_loader.py:317-320`) currently warns and returns `[]`, so a project would start on stale DB rows. Raise `ConfigLoadError` when the root directory itself is absent, keeping the existing warning for a root that exists but holds no definitions
- [x] 6.3 Make that diagnostic upgrade-aware: name the unresolved path, and when the value came from the default rather than the project's settings, state that it changed from `./tasks/` and give the setting that restores the old layout
- [x] 6.4 `git mv examples/demo/tasks examples/demo/plan` (26 tracked files)
- [x] 6.5 Update `recordflow_paths` in `examples/demo/settings.toml` to `["./plan/workflows"]` — the demo's only internal `tasks/` reference
- [x] 6.6 Update `README.md` lines 129 and 139 to the `examples/demo/plan/...` paths

## 7. Tests

- [x] 7.1 Scaffold root resolves inside `Path(clarinet.__file__).parent` — the #472 regression guard; confirm it fails against pre-change code
- [x] 7.2 Payload manifest: every file `init` writes is present in the package payload dir
- [x] 7.3 `init` into a tmp dir yields `plan/`, `.claude/CLAUDE.md`, the managed rules, and no `tasks/` directory
- [x] 7.4 `init` over a directory with a pre-existing `settings.toml` leaves it unchanged and reports the skip
- [x] 7.5 `.gitignore` and `.env.example` are materialized with dotted names and matching content; the package payload contains no `.gitignore`
- [x] 7.6 An edited `.claude/CLAUDE.md` survives `agent update` byte-for-byte
- [x] 7.7 Pruning: a dropped managed doc is removed; an unheadered file in the managed dir is not; an edited managed `overview.md` is migrated to `.claude/CLAUDE.md` with its edits and without the header; an existing `.claude/CLAUDE.md` is not clobbered
- [x] 7.8 `config_tasks_path` defaults to `./plan/`; a missing root raises rather than returning `[]`; a root that exists but holds no definitions still warns and returns `[]`; the message names the flip only when the value came from the default

## 8. Docs, changelog, release

- [x] 8.1 Rewrite `clarinet/docs/project-setup.md`: drop "Templates are copied from `examples/`", drop the `--template`/`--list-templates` block, document the single `clarinet init` and the `./plan/` default
- [x] 8.2 Copy the result to `.claude/rules/project-setup.md` **byte-identically** — `project-setup` is in `DEEP_DOCS` and `test_deep_docs_identical_to_rules_seeds` fails if only one twin changes
- [x] 8.3 Add the CHANGELOG breaking entry with the downstream migration table (explicit `config_tasks_path`, `agent update` for the overview migration, drop `--template`)
- [ ] 8.4 Bump `pyproject.toml` version to `0.11.0` — **deliberately skipped**: this repo cuts versions in standalone `chore:` commits that get tagged (`.claude/rules/release.md`), and the CHANGELOG accumulates under `## Unreleased`. Bumping inside a feature PR pre-empts the release decision and conflicts with the other open breaking PR (#497). Entries added under `## Unreleased` instead.
- [ ] 8.5 Not taken (optional, out of scope): stop shipping `examples/demo/.claude/settings.local.json` in the sdist
- [x] 8.6 Run `./scripts/run_tests.sh -k "agent_scaffold or scaffold or config_loading or cli_init or templates" -q` (validated: **100 of 3329** collected pre-change; expect it to grow with the new tests), then `make check`
