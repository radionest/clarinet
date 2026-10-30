# Design: unify-project-scaffold

## Context

Clarinet delivers two things to a downstream project: a **project skeleton**
(settings, `plan/` tree) and **agent documentation** (`.claude/` rules). Today
two mechanisms deliver overlapping subsets of both:

| Mechanism | Source | In wheel | Refreshable |
|---|---|---|---|
| `clarinet agent init\|update` | `clarinet/docs/agent/claude/*.md` | ✅ (`artifacts`) | ✅ |
| `clarinet init --template research` | `examples/project_template/` | ❌ | ❌ |

The second is broken end-to-end for pip installs (#472) because
`find_examples_dir()` (`clarinet/cli/templates.py:34-38`) resolves
`Path(clarinet.__file__).parent.parent / "examples"` — i.e. `site-packages/examples`,
which does not exist. Verified: the v0.10.20 wheel contains 335 entries, 14 under
`clarinet/docs/`, and **0** under `examples/`.

Because both mechanisms carry the same agent-doc payload, the copies have drifted
in both directions (#485 → template only; #448 → packaged only), guarded by a test
that checks filenames but not content. The practical damage: `unique_by`,
`{parent_id}` and `allow_path_collision` are documented **only** in the copy that
cannot be delivered.

Constraints the design must respect:

- Only files under `clarinet/` reach the wheel (`packages = ["clarinet"]`).
- The repo `.gitignore` is a **denylist**; hatchling selects files via VCS status,
  so anything git ignores silently leaves the wheel.
- Dotfiles *do* ship (`clarinet/ohif/.gitignore` is in the wheel), but a
  `.gitignore` inside the package is **live** and governs what git tracks there.
- `project-setup` is in `DEEP_DOCS`: `clarinet/docs/project-setup.md` and
  `.claude/rules/project-setup.md` are asserted byte-identical.
- Alembic is project-level — this change ships no migration.

## Goals / Non-Goals

**Goals:**

- `clarinet init` works identically from a wheel and from a source checkout.
- Exactly one authority per payload: skeleton from the package scaffold, agent
  docs from `scaffold_agent_docs()`.
- A project's own `CLAUDE.md` is owned by the project and never clobbered.
- `plan/` is the single project layout convention, including for projects that
  never set `config_tasks_path`.
- Regressions in payload shipping are caught by unit tests, without building a
  wheel in the test loop.

**Non-Goals:**

- A general template *system*. One scaffold, no registry, no `--template`.
- Shipping `examples/demo` to users. It stays repo reading material.
- Renaming the `config_tasks_path` setting itself (the name still says "tasks");
  that is a separate, larger breaking change.
- Retrofitting existing downstream projects. They opt in by upgrading.
- Any DB or API change.

## Decisions

### D1 — Payload moves inside the package to `clarinet/scaffold/`

- **Choice**: `examples/project_template/{settings.toml, settings.custom.toml,
  .env.example, .gitignore, plan/**}` → `clarinet/scaffold/`, resolved via
  `Path(clarinet.__file__).resolve().parent / "scaffold"`, with
  `clarinet/scaffold/**/*` added to `[tool.hatch.build] artifacts`.
- **Rationale**: mirrors `clarinet/docs`, the mechanism in the same codebase that
  already solves exactly this problem (`agent_scaffold._package_docs_dir()`).
- **Alternatives rejected**:
  - *Force-include top-level `examples/`* — would place a generic top-level
    `examples/` directory into `site-packages`, colliding with any other package
    doing the same, and leaves the resolution rooted at `.parent.parent`.
  - *Declare templates checkout-only and fail loudly* — honest, but leaves the
    `plan/` skeleton unreachable for pip users, who are the audience that needs it.

- **Implementation note (found during apply)**: the payload's `.py` files move
  into the framework lint/typecheck path (`ruff check clarinet/`,
  `mypy clarinet/`), silently losing the `examples/*` per-file-ignores they had
  — including `E712`, which exempts the RecordFlow DSL idiom
  `F.is_good == True` (an expression builder, not a truthiness test). Fixed by
  mirroring that ignore list onto `clarinet/scaffold/**` in `.ruff.toml`, plus a
  `clarinet_plan.*` mypy override with `ignore_missing_imports` (the anchor
  package is assembled at runtime and has no on-disk source). Type checking is
  kept rather than excluded — a payload file with a real type error would
  otherwise ship broken into every new project.

### D2 — Retire the template system; `clarinet init` is a single path

- **Choice**: remove `--template` and `--list-templates`; delete
  `clarinet/cli/templates.py`; `init_project(path)` loses its `template` parameter.
  `demo` is discoverable through the README and a pointer line in `init`'s
  completion output.
- **Rationale**: with `demo` reclassified as reading material, one template
  remains, and a one-entry registry is not a feature. More importantly, the
  bare-vs-template split is the *cause* of the layout divergence: bare `init`
  emits `tasks/` while the template emits `plan/`, and `copy_template` mkdirs an
  orphan `tasks/` on top of the template's `plan/`.
- **Alternatives rejected**: keeping the flag for future templates — preserves two
  code paths that must agree on layout, which is precisely what failed.

### D3 — Agent docs come only from `scaffold_agent_docs()`

- **Choice**: delete `examples/project_template/.claude/` entirely; `init` calls
  `scaffold_agent_docs("claude", project_dir=…, mode="init")` after copying the
  skeleton. `test_project_template_ships_same_section_rules` is deleted with its
  subject.
- **Rationale**: the template copy was worse on every axis — unresolved
  `<clarinet>/…` links, unnamespaced destination, no managed header, no refresh
  path, presence-only test guard — and had already drifted in both directions.
- **Alternatives rejected**: keeping both and adding a byte-equality test — the
  twins *cannot* be byte-identical (different link form), which is the loophole
  that allowed the drift in the first place.

### D4 — `overview.md` is reclassified from managed doc to seed

- **Choice**: split the payload in `agent_scaffold` into a **managed** set
  (`definitions`, `workflows`, `anonymization`, `slicer`, `schemas`, `utils`,
  `scripting` → `.claude/rules/clarinet/`, managed header, rewritten by `update`)
  and a **seed** set (`overview.md` → `<project>/.claude/CLAUDE.md`, no managed
  header, written only when absent, never touched by `update`).
- **Rationale**: `overview.md`'s body instructs the user to replace its contents,
  while its header forbids editing and `update` overwrites it. The file is
  project-owned by intent and must be delivered as such. Placing the split in
  `agent_scaffold` (not in `init`) fixes existing projects too — they carry this
  bug today.
- **Alternatives rejected**: having `init` write the CLAUDE.md directly — leaves
  `agent init` clobbering for every project not created by `init`.

### D5 — `agent update` prunes orphans, but *migrates* `overview.md`

- **Choice**: `update` removes files in the managed dir that are no longer in the
  payload. One-release exception: if `overview.md` is being pruned and
  `<project>/.claude/CLAUDE.md` does not exist, it is **moved** there instead of
  deleted.
- **Rationale**: without pruning, every already-scaffolded project keeps a stale
  `overview.md` forever after D4. But a user who followed its "replace this
  file's contents" instruction has real work in it — deleting that would commit
  the very bug D4 exists to fix. Moving preserves their content and lands it at
  the correct path.
- **Alternatives rejected**: content-hash comparison to decide "unmodified,
  therefore safe to delete" — more machinery, and the move is correct in both the
  modified and unmodified case. The exception is dated: it can be dropped once
  the 0.11.0 upgrade wave has passed.

- **Implementation note (found during apply)**: pruning must run *before* the
  seed is written, not after. `update` writes the seed whenever the target is
  absent — so seeding first occupies `.claude/CLAUDE.md` with fresh payload text
  and the migration branch then sees an occupied target, deleting the user's
  edited `overview.md` instead of migrating it. Order is load-bearing.
- **Implementation note**: "carries the managed header" cannot be
  `text.startswith(marker)` — `_with_header` places the header *after* the YAML
  frontmatter, and every managed doc except `overview.md` has `paths:`
  frontmatter. A `startswith` test would classify all of them as project-owned
  and prune nothing. `_is_managed()` mirrors the writer's two placements.

### D6 — Payload dotfiles are stored undotted and renamed at scaffold time

- **Choice**: store `gitignore` and `env.example` (no leading dot) in
  `clarinet/scaffold/`; write them as `.gitignore` / `.env.example` into the
  target project.
- **Rationale**: a `.gitignore` living inside the package is applied by git to
  that directory. The template's copy carries `data/`, `*.db`, `*.log`,
  `__pycache__/` — harmless today because nothing matches, but the day a
  `plan/data/` example is added it silently leaves git, then silently leaves the
  wheel. That is #472 reintroduced one level down. Renaming removes the failure
  mode by construction rather than by vigilance.
- **Alternatives rejected**: ship verbatim and rely on the manifest test —
  detection instead of prevention, for a two-line saving.

### D7 — `config_tasks_path` default flips to `./plan/` now

- **Choice**: change the default outright in `clarinet/settings.py`; add an
  upgrade-aware hint to the plan-root resolution failure.
- **Rationale**: every doc, rule and scaffold says `plan/`; three of the four
  active downstream projects already set it explicitly. Leaving the default at
  `./tasks/` keeps a silent trap for anyone who omits the setting.
- **The diagnostic is load-bearing, not cosmetic.** Verified against current
  code: in python mode a missing root makes `load_python_config`
  (`clarinet/config/python_loader.py:317-320`) log a warning and return `[]`,
  which reconciles zero record types. With `config_delete_orphans=False` (the
  default) the existing DB rows survive, so the application **starts normally**
  and merely stops tracking its own configuration. Flipping the default without
  converting that path to a hard failure would turn a loud one-line migration
  into an invisible drift. The abort is therefore part of this decision, not a
  follow-up polish.
- **Alternatives rejected**:
  - *Warn now, flip later* — two releases of carrying both behaviours.
  - *Scaffolds only* — zero risk and zero improvement for existing projects.
- **Known casualties**: `clarinet_carcinomatosis` (setting commented out, real
  `tasks/` dir) and `examples/demo`. Both are addressed in the migration plan.

### D8 — Harvest strictly precedes deletion

- **Choice**: backport `{parent_id}`, `allow_path_collision` and `unique_by`
  (+ the "per level context" wording) into
  `clarinet/docs/agent/claude/definitions.md` as the **first** task; delete
  `examples/project_template/.claude/` only after it lands.
- **Rationale**: that template copy is the only downstream-facing home for this
  content. Reversing the order loses it permanently, silently, in a diff that
  otherwise looks like pure cleanup.

### D9 — `init` never overwrites an existing file

- **Choice**: the skeleton copy skips any file that already exists and reports
  what it skipped; it does not use blanket `dirs_exist_ok` overwriting. This
  composes with `scaffold_agent_docs(mode="init")`, which already refuses a
  populated managed dir without `force`.
- **Rationale**: `clarinet init .` in an existing project currently overwrites
  `settings.toml`. Write-once is the same principle D4 applies to the seed, and
  matches the project's safe-default preference.

### D10 — Test the payload without building a wheel

- **Choice**: two structural guards instead of a wheel-build test — (1) the
  scaffold root resolves *inside* `Path(clarinet.__file__).parent`; (2) a payload
  manifest asserting every file the scaffold needs is present in the package dir.
- **Rationale**: (1) is the direct #472 regression guard and fails on today's
  code. (2) is what catches the D6 failure class — a gitignore or `artifacts`
  change silently dropping payload files — at unit-test speed.

### D11 — The scaffold does not create a project-root `scripts/`

- **Choice**: `clarinet init` materializes no project-root `scripts/` directory.
  The payload keeps `plan/scripts/` (Slicer scripts, real content); the root
  `scripts/` for operational one-shot scripts stays out.
- **Rationale**: the payload mechanism copies *files* — an empty directory needs
  a placeholder, which is exactly the noise this change removes (the old
  `("tasks", "static", "data")` mkdir loop created orphan directories beside the
  template's own `plan/`). More decisively, `clarinet.scripting` is not
  implemented: `add-scripting-frame` sits at 0/12. Materializing the directory
  would advertise a frame that does not exist.
- **On the dangling reference**: `overview.md` naming a convention the scaffold
  does not materialize is acceptable — it is now a project-owned seed (D4) whose
  own body tells the user to replace it. The directory appears when the user
  writes their first operational script.
- **Alternatives considered**: (a) create it with a `.gitkeep` — rejected, D6
  already removes in-package dotfiles and a placeholder for an unimplemented
  frame is worse than absence; (b) strip the `scripts/` paragraph from
  `overview.md` — rejected, the convention is real and `scripting.md` ships
  today; deleting the pointer would have to be undone by `add-scripting-frame`.
- **Handoff**: `add-scripting-frame` adds `scripts/<example>.py` to
  `clarinet/scaffold/` alongside the frame it documents. That is one payload
  file and no interface change — the manifest test (D10) is the only thing to
  extend.

### D12 — Corrections from the pre-PR review

Four defects the review caught against the ACs above, and how they were resolved:

- **`init` was not re-runnable.** `init_project` called `scaffold_agent_docs`
  with `mode="init"` and no `force`, so a second run raised `AgentScaffoldError`
  *after* the payload loop had already written files — a half-scaffolded
  directory and a contradiction of the "safe to re-run" line in the setup doc.
  Fixed by passing `force=True`: project-owned files are still never
  overwritten, and only the framework-managed docs are refreshed.
- **`init --force` could strand a legacy overview.** Pruning ran only for
  `mode == "update"`, so a forced init on a project scaffolded before the split
  wrote the seed and left the legacy managed `overview.md` in place; the *next*
  `update` then found the seed target occupied and deleted the user's edits.
  Fixed by running the prune/migrate pass in every mode — it is a no-op on a
  freshly created managed dir.
- **The upgrade hint keyed off the value, not provenance.** The shipped scaffold
  writes `config_tasks_path = "./plan/"` explicitly, so a value comparison told
  *every* scaffolded project to restore a `tasks/` layout it never had.
  Membership in `settings.model_fields_set` expresses provenance directly
  (verified: env var and TOML source both mark it set; unset stays absent).
  Note that `init_settings` is discarded by `settings_customise_sources`, so a
  constructor kwarg is not a valid probe for this.
- **The abort only covered Python mode.** `config_mode` defaults to `"toml"`,
  whose branch in `reconcile_config` warned and returned an empty
  `ReconcileResult()` — the very degradation the requirement targets, in the
  more common mode. The guard moved into `ensure_config_root_exists()` and is
  now called from `reconcile_config` before the mode dispatch, so both modes
  abort. Widens the breaking change: a TOML project whose root is missing now
  fails to start.

## Risks / Trade-offs

- **[Risk]** Downstream projects on the `./tasks/` default fail to start after
  upgrade. → *Mitigation*: minor version bump to 0.11.0; CHANGELOG breaking entry
  with the exact one-line fix; the resolution error itself names the flip.
- **[Risk]** A user's edited `overview.md` is destroyed by the new pruning. →
  *Mitigation*: D5 migrates rather than deletes when the target CLAUDE.md is absent.
- **[Risk]** Future payload files silently dropped from the wheel by a gitignore
  or `artifacts` change. → *Mitigation*: D6 removes the in-package `.gitignore`;
  D10's manifest test fails loudly if a payload file goes missing.
- **[Risk]** Editing only one half of the `project-setup.md` twin breaks the
  suite. → *Mitigation*: called out as an explicit task, not left to be
  rediscovered by a red test.
- **[Trade-off]** Removing `--template`/`--list-templates` is breaking for source
  -checkout users who scripted against them. → Accepted: the flags never worked
  for the installed case, and the surface is tiny.
- **[Trade-off]** `clarinet/scaffold/` adds ~10 payload files to the wheel. →
  Accepted: `clarinet/docs` already established the pattern and the cost.
- **[Trade-off]** The seed freezes its `{{CLARINET_DOCS}}` links at first write.
  Managed docs get their links re-resolved on every `update`; the seed, being
  write-once, does not — so a project whose clarinet moves to a different
  install path keeps stale absolute links in `.claude/CLAUDE.md`. Accepted:
  it follows directly from the seed being project-owned (D4), the links are
  soft references an agent can recover from, and the same list re-resolved
  correctly lives in the managed rules. Raised by the pre-PR review; revisit by
  moving the framework-doc index out of the seed into a managed doc if it bites.
- **[Trade-off]** The D5 `overview.md` migration is a dated special case. →
  Accepted: it buys a clean upgrade for the one release where it matters, and is
  cheap to remove afterwards.

## Migration Plan

Implementation order (the first item is a hard gate on the fifth):

1. Harvest the three `definitions.md` rows into the packaged copy.
2. Move the payload into `clarinet/scaffold/` (dotfiles undotted); add the
   `artifacts` entry.
3. Split managed/seed in `agent_scaffold`; add pruning with the `overview.md`
   migration.
4. Rewire `init` to the single path; delete `clarinet/cli/templates.py` and the
   argparse flags.
5. Delete `examples/project_template/.claude/` and its presence test.
6. Flip the `config_tasks_path` default; add the resolution-failure hint.
7. Rename `examples/demo/tasks` → `plan`; update `recordflow_paths` and README
   lines 129/139.
8. Tests, then docs (both `project-setup.md` twins byte-identically), CHANGELOG,
   version bump to 0.11.0.

**Downstream migration** (published in the CHANGELOG):

| Project shape | Action |
|---|---|
| Sets `config_tasks_path = "./plan/"` | None |
| Omits it, uses `plan/` | None |
| Omits it, uses `tasks/` | Add `config_tasks_path = "./tasks/"`, or rename to `plan/` |
| Ran `clarinet agent init` before 0.11.0 | Run `clarinet agent update`; an edited `overview.md` is moved to `.claude/CLAUDE.md` |
| Scripted `clarinet init --template …` | Drop the flag |

**Rollback**: revert the release. The change writes no persistent state; a project
already scaffolded by 0.11.0 keeps working against 0.10.x provided it sets
`config_tasks_path` explicitly.

## Open Questions

- ~~Should the scaffold create a project-root `scripts/` directory?~~
  **Resolved during apply — see D11: no.** The frame it would advertise
  (`clarinet.scripting`) is unimplemented; `add-scripting-frame` adds the payload
  file when it ships the frame.
- **Is the `examples/demo/.claude/settings.local.json` sdist leak in scope?** It
  is a per-developer file currently shipped in the published sdist — a cheap fix
  while packaging is already being touched, but strictly unrelated to #472.
