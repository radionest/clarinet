# Retrospective: unify-project-scaffold

> Written: 2026-07-27 (after verify passed with warnings)
> Commit range: `d0df3c6..acd6680`
> Worktree: `/home/nest/Projects/clarinet/.claude/worktrees/unify-scaffold` (branch `worktree-unify-scaffold`, pushed, PR not yet opened)

---

## 0. Evidence

- **Commit range**: `d0df3c6..acd6680` (8 commits)
- **Diff size**: +679 / −1995 across 69 files (net −1316; the duplicated template tree accounted for ~1602 deletions)
- **Tasks done**: 42/44 (2 open by decision — version bump deferred to the release process, sdist leak out of scope)
- **Active hours**: ~4 (including a ~1h pre-PR review cycle)
- **Subagent dispatches**: 1 (`pr-diff-reviewer`; the schema's `subagent-driven-development` executor was not used — see §4)
- **New external dependencies**: none. Two build/tooling config additions only: `clarinet/scaffold/**/*` in hatch `artifacts`, and a `clarinet_plan.*` mypy override
- **Bugs encountered post-merge**: n/a (not merged)
- **OpenSpec validate state at verify time**: pass (6 items, 0 invalid); delta specs await post-merge archive
- **Test coverage signal**: full suite 3131 passed / 0 failed / 204 skipped; change gate 158 passed (original selector baseline 100 pre-change); `make check` clean across 231 source files

Commit chain:

```
2fcd8e0 docs(agent): backport unique_by, {parent_id} and allow_path_collision
134dd1f feat(cli): move the project scaffold payload inside the package
c333671 feat(agent): make overview.md a project-owned seed and prune stale managed docs
f001750 feat(cli)!: single-path clarinet init, retire --template
7b8d955 refactor: drop the duplicated agent-doc copy from the project template
455ec30 feat(config)!: default config_tasks_path to ./plan/ and abort on a missing root
88a5aff docs: document the single-path init, the seed CLAUDE.md and the ./plan/ default
acd6680 fix: address pre-PR review — re-runnable init, prune in every mode, TOML abort
```

---

## 1. Wins

- The bug was proved against a **published artifact**, not inferred from config. Downloading the v0.10.20 wheel and counting entries (335 total, 14 `clarinet/docs/`, **0** `examples/`) turned "`--template` looks broken" into a measured fact before any design work started.
- Framing #472 as a *duplicated delivery path* rather than a packaging typo was the load-bearing insight. Git history showed bidirectional drift (#485 landed in the template only, #448 in the packaged copy only), which meant `unique_by` documentation was reaching **no** downstream user. Task 1 (`2fcd8e0`) harvested it before `7b8d955` deleted the tree — the plan's hard gate did its job.
- The harvest diff predicted its own result exactly: only `definitions.md` was behind, by precisely the 3 rows the plan named; `schemas`/`overview` were packaged-ahead; the rest differed only in link form.
- **A doc line caught a real defect.** Writing "safe to run over a partially set-up directory" in `project-setup.md` created a claim the code did not satisfy — the review then found `clarinet init` crashed on second run. Documenting intended behavior surfaced a bug that the tests had missed.
- End-to-end verification at the level users experience: built an actual wheel (12 payload files present, `examples/` still 0), and drove the real CLI through both blocker scenarios rather than trusting unit tests alone.
- The `plan/` rename was load-bearing, not cosmetic: `examples/demo/settings.toml` never set `config_tasks_path`, so the demo ran purely on the default. Without `git mv tasks plan` the demo would have broken silently.

## 2. Misses

- 🔴 **`clarinet init` was not re-runnable** (`acd6680`). `init_project` called `scaffold_agent_docs(mode="init")` with no `force`, so a second run raised *after* the payload loop had already written files — leaving a half-scaffolded directory. Neither my tests nor the plan covered "run it twice", the most obvious user action after a failed first attempt.
- 🔴 **A data-loss path survived my own ordering fix** (`acd6680`). I caught that prune must precede the seed write, wrote it up as a design note, and still missed that `init --force` skipped prune entirely — so a forced init stranded the legacy `overview.md`, and the *next* `update` deleted the user's edits. I reasoned about ordering within one mode and never enumerated the mode × force matrix.
- 🔴 **The upgrade hint keyed off a value, not provenance** (`acd6680`). The spec said "was not set by the project"; I implemented `settings.config_tasks_path == default`. Because the shipped scaffold writes `./plan/` explicitly, *every* scaffolded project would have been told to restore a `tasks/` layout it never had — the exact population the feature exists to help.
- 🔴 **The abort covered the minority mode** (`acd6680`). `config_mode` defaults to `"toml"`, and I only guarded `load_python_config`. The spec text was mode-independent; I implemented against the file I happened to be reading.
- 🟡 **My first probe of `model_fields_set` was invalid and I nearly acted on it.** `Settings(config_tasks_path=...)` returned `False`, so I told the user the reviewer's proposed fix would break the feature. `settings_customise_sources` discards `init_settings` — the kwarg never reached the model. Re-probing through env var and TOML file showed `True`. I stated a wrong conclusion confidently before checking how the object is actually constructed.
- 🟡 **Moving code between trees silently changed its lint contract** (`134dd1f`). The payload carried an `examples/*` per-file-ignore for `E712` covering the RecordFlow DSL idiom `F.is_good == True`; moving it into `clarinet/` dropped that. Caught by running the linters, but nothing in the plan anticipated that a `git mv` changes which rules apply.
- 🟡 **The twins drifted because I edited after copying** (`88a5aff`). I `cp`'d `project-setup.md`, then made one more edit to the source only — the golden guard failed. The plan explicitly warned about this trap and I walked into it anyway, because the warning was attached to the copy step, not to "any later edit".
- 📌 A `git add` with an already-staged `git rm` pathspec aborted the whole add, producing a commit with only the deletion (`f001750` needed amending).

## 3. Plan deviations

| Plan task | What changed | Why |
|---|---|---|
| Task 3, step 5 | Prune moved *before* the seed write, and later made unconditional | The plan's seed-then-prune order destroys an edited `overview.md` on `update`; unconditional prune additionally covers `init --force` |
| Task 3, step 5 | `text.startswith(_MANAGED_MARKER)` replaced by `_is_managed()` | `_with_header` places the header *after* YAML frontmatter, and every managed doc except `overview.md` has frontmatter — a `startswith` test would have pruned nothing |
| Task 2 | Added `.ruff.toml` per-file-ignores + `clarinet_plan.*` mypy override | Payload `.py` moved into the framework lint/typecheck path (§2) |
| Task 5 | Also edited `test_translated_agent_docs_have_no_cyrillic` | The plan listed only one orphaned test; this one also consumed the `PROJECT_TEMPLATE_*` constants |
| Task 3 | Updated 6 pre-existing tests | They asserted on the written `overview.md`, which is exactly what moved |
| Task 6 | Also fixed 3 hardcoded `"./tasks/"` fallbacks in `record.py` | Not in the plan; found by grepping the flip's blast radius |
| Task 6 | Guard extracted to `ensure_config_root_exists`, called from `reconcile_config` | Extends the abort to TOML mode (user-approved scope widening) |
| Task 7, step 5 | Version bump **not** performed | Repo cuts releases via standalone tagged `chore:` commits; bumping in a feature PR pre-empts that and collides with open PR #497. The startup message was made version-agnostic as a consequence |
| Task 1.1 | Resolved as "no root `scripts/`" (D11) | `clarinet.scripting` is unimplemented (`add-scripting-frame`, 0/12) |
| — | `tests/conftest.py` session fixture added | 6 lifespan tests booted with no config root at all once TOML mode began aborting |
| — | 16 `/fake/path` sentinels replaced | The new precondition rejects a path that never existed |

## 4. Skill / workflow compliance

| Skill | Used |
|---|---|
| superpowers:brainstorming | ⚠️ partial |
| superpowers:writing-plans | ✓ |
| superpowers:using-git-worktrees | ⚠️ substituted |
| superpowers:subagent-driven-development | ✗ |
| (transitive) superpowers:test-driven-development | ✓ |
| (transitive) superpowers:requesting-code-review | ✓ |
| superpowers:finishing-a-development-branch | ⏳ not yet reached |

### Deliberately Skipped Skills

- **`superpowers:subagent-driven-development`**
  - **What was skipped**: the entire executor. `plan.md` was executed inline in the apply session instead of dispatching a fresh subagent per task with two-stage review.
  - **Why this cycle**: the session's system prompt carries a standing instruction — *"Do not call the AgentTool unless the user requested it"* — which directly contradicts the schema's apply instruction mandating SDD. I resolved the conflict toward the user-level instruction (per the superpowers precedence rule that user instructions override skills) and stated the deviation in the completion report rather than silently substituting. Reinforcing signal: the user's `/compact` argument was *"keep plan and all you need for implementation"*, i.e. carry the plan in **this** context.
  - **How to prevent recurrence**: `CLAUDE.md trigger` — the adopter fragment should state that when a project or user instruction forbids subagent dispatch, `superpowers-bridge` is the wrong schema and the built-in `spec-driven` schema should be selected at `/opsx:new` time. The schema's own apply instruction already says it "does NOT support executing-plans as a fallback"; it should say the same about environments where subagents are unavailable **by policy**, not only by platform.
  - **Cost incurred**: measurable. Four blockers/spec-violations (§2) reached the review that per-task code-review gates would likely have caught earlier — SDD's `requesting-code-review` runs after *each* task, whereas here the first independent eyes arrived after all 7 tasks were committed.

- **`superpowers:brainstorming`** (partial)
  - **What was skipped**: the skill invocation, not the activity. Brainstorming happened as the `/opsx:explore` session; `brainstorm.md` was written as its decision log.
  - **Why this cycle**: the exploration had already produced and settled the decision chain (Q1–Q8, including three `AskUserQuestion` resolutions). Re-invoking would have re-asked questions the user had already answered.
  - **How to prevent recurrence**: `schema graph fix` — `superpowers-bridge` should recognise a completed `/opsx:explore` as satisfying the brainstorm artifact, or the brainstorm instruction should say "capture the prior exploration if one occurred" instead of always invoking the skill.

- **`superpowers:using-git-worktrees`** (substituted)
  - **What was skipped**: the skill; a worktree *was* created, via the project's own `EnterWorktree` tool.
  - **Why this cycle**: `CLAUDE.md` mandates `EnterWorktree` and a `require-worktree.sh` hook enforces it. Using the superpowers skill would have produced a worktree outside `.claude/worktrees/` that the project's hooks do not recognise.
  - **How to prevent recurrence**: `one-off — schema boundary case`. It is a boundary because the schema assumes it owns workspace creation, while this project has a hook-enforced mechanism of its own; the outcome (isolated worktree) is identical, so only the tool differs. Worth noting in the adopter fragment so future retros don't re-litigate it.

## 5. Surprises

- **`settings_customise_sources` discards `init_settings`.** Constructor kwargs do not reach `Settings`, which makes `Settings(field=...)` useless as a probe. This invalidated my first provenance measurement and nearly caused me to reject a correct review finding.
- **The repo root has a tracked `tasks/` directory** holding TOML exports leaked by earlier test runs (`e9db972` shows this has been cleaned up before). It masked the flip locally: `./tasks/` existed, so nothing failed until the default moved to `./plan/`.
- **The demo relied on the default**, not an explicit setting — discovered only by grepping, and it made the rename mandatory rather than tidy.
- **`git mv` changes lint scope.** I expected moving files to be semantically inert; it silently dropped an `E712` exemption that existed precisely because the DSL idiom looks like a bug.
- **Writing the docs found the bug.** The "safe to re-run" sentence was aspirational; asserting it in prose is what exposed that the code did the opposite.
- **The review's proposed fix was right and my rebuttal was wrong** — a useful reminder that "verify the agent's claim" must include verifying my own counter-claim with an equally valid probe.

## 6. Promote candidates → long-term learning

- [ ] 🔴 **Enumerate the full mode × flag matrix for any function whose behavior branches on a mode parameter** → **Promote to memory** (type: feedback)
  > **Why**: I found and documented the prune/seed ordering bug for `mode="update"`, then shipped a data-loss path because `init --force` reached the same legacy state through a branch I never enumerated. Reasoning carefully about one branch created false confidence about all of them.
  > **How to apply**: when a function takes a mode/flag that gates a destructive step (delete, unlink, overwrite), write out every combination and state the expected outcome for each before implementing — especially where one combination is an "escape hatch" the error message actively recommends.

- [ ] 🔴 **When a spec says "was not set", implement provenance, not value equality** → **Promote to memory** (type: feedback)
  > **Why**: `settings.config_tasks_path == default` looked equivalent to "came from the default" and was not: the shipped scaffold sets the same value explicitly, so the upgrade hint misfired on exactly the projects it was written for.
  > **How to apply**: any conditional phrased as "if the user didn't configure X" — reach for the framework's provenance signal (`model_fields_set`, `__fields_set__`, `is_default()`), and verify it through a **real** configuration source, since constructor kwargs may be discarded by custom settings sources.

- [ ] 🔴 **A mode-independent requirement must be implemented at the dispatch point, not in one branch** → **Promote to memory** (type: feedback)
  > **Why**: the spec said "the framework SHALL abort"; I implemented it in `load_python_config` only, leaving the default (`toml`) mode with the exact silent degradation the requirement targets.
  > **How to apply**: when a requirement is written without naming a mode/backend, grep for the dispatcher (`if mode ==`) and place the guard above it — then check which branch is the *default*, because that is the one most users are on.

- [ ] 🟡 **Moving files between trees can change which lint/type rules apply** → **Promote to project CLAUDE.md** (Pre-commit Checklist section)
  > **Why**: relocating the scaffold payload into `clarinet/` silently dropped the `examples/*` `E712` exemption that covers the RecordFlow DSL idiom `F.is_good == True`, and added the files to `mypy clarinet/`.
  > **How to apply**: after any `git mv` that crosses a top-level directory, run `make check` before writing tests, and check `.ruff.toml` per-file-ignores and `[tool.mypy] exclude` for entries scoped to the **old** path.

- [ ] 🟡 **Re-copy byte-identical twins as the last action before committing, never at the point of first edit** → **Promote to project CLAUDE.md** (Documentation Structure / golden-guard note)
  > **Why**: `project-setup.md` twins drifted because I copied, then edited the source once more. The plan warned about the trap; the warning was attached to the copy step, so it didn't fire on the later edit.
  > **How to apply**: for any file in `DEEP_DOCS`, make the `cp` + `cmp` the final pre-commit step, and re-run it after *any* subsequent edit to either twin.

- [ ] 🟡 **When a project/user rule forbids subagents, pick the schema that doesn't require them** → **Promote to schema** (`superpowers-bridge` apply instruction + adopter CLAUDE.md fragment)
  > **Why**: this cycle's apply instruction mandated `subagent-driven-development` while the session forbade agent dispatch. Resolving it inline meant the first independent review arrived after all 7 tasks were committed rather than after each — and four defects reached that review.
  > **How to apply**: at `/opsx:new`, if the environment or user instructions preclude subagent dispatch, choose the built-in `spec-driven` schema. The bridge schema's "no manual fallback" warning should cover policy-disabled subagents, not just platforms lacking them.

- [ ] 🟡 **Verify a rebuttal as rigorously as the claim being rebutted** → **Promote to memory** (type: feedback)
  > **Why**: I told the user a review finding's proposed fix "would silently break the feature", based on a probe (`Settings(field=...)`) that a custom `settings_customise_sources` renders meaningless. The finding was correct.
  > **How to apply**: before contradicting a reviewer, agent, or user with an empirical result, confirm the probe exercises the real code path — construct the object the way production does, not the way that is convenient.

- [ ] 📌 **`git add <already-staged-deletion-path>` aborts the entire add** → **One-off**
  > **Why**: including a `git rm`'d path in a later `git add` list fails the whole command, so a commit captured only the deletion and needed amending. Mechanical, low cost, unlikely to generalize into a rule worth loading every session.

- [ ] 📌 **Extend the release workflow's wheel-content check to `clarinet/scaffold/`** → **One-off** (follow-up issue)
  > **Why**: D10's structural guards catch a missing payload file but not a `pyproject.toml` `artifacts` regression. `release.yml` already verifies `clarinet/static/` is in the wheel; adding `clarinet/scaffold/` closes the last gap for the #472 class of bug.

---

> **Update 2026-07-27** — §0–§6 above were written at `acd6680`, before review
> rounds 3 and 4. They are left intact as the record of what was known then.
> The analysis below supersedes §2 (Misses) and §6 (Promote candidates).

## 7. Post-write addendum: the recurring class

Three further blockers landed after the retrospective was written, and they were
**one class**: a payload file contradicting another payload file. Each was
introduced by the fix for the previous one.

| Round | Defect | Introduced by |
|---|---|---|
| 2 | `${CLARINET_PORT}` placeholders reached `int` fields — every scaffolded project's commands exited 1 | pre-existing in the template; **made live** by fixing the delivery path |
| 3 | New header claimed "SQLite dev defaults" while `settings.toml` pins Postgres | my round-2 fix |
| 4 | `# port = 8000` in `settings.custom.toml` vs `port = 8111` in `settings.toml` | my round-3 fix |

### What actually went wrong

- 🔴 **Fixing a broken delivery path makes latent payload defects live for the
  first time.** The `${VAR}` breakage had existed in the template for as long as
  the template had, and was harmless *because `--template` never worked from a
  pip install* — the exact bug this change set out to fix. Nothing in the
  brainstorm, design, or plan asked "what has never been exercised because the
  broken path shielded it?" That question would have found it before round 2.
- 🔴 **I corrected a claim in one file and authored the same wrong claim in its
  sibling, in the same commit — twice.** Round 3's blocker: `96d1b8d` fixed
  "Dev config (SQLite…)" in `project-setup.md` and simultaneously wrote "runs on
  the SQLite dev defaults" into `settings.custom.toml`. Round 4's: the commit
  that pinned `port = 8111` left `# port = 8000` in the sibling. Editing one
  file at a time, I never re-read the *set*.
- 🟡 **Four review rounds, four real blockers.** The rate never decayed. Each
  round's fix was verified in isolation and each introduced the next defect —
  which is the signature of a missing invariant, not of carelessness.

### What finally addressed it

`test_payload_settings_agree_on_port` (`b723f5c`) — reads the port from *both*
payload TOMLs including commented lines, and fails when they diverge. This is
the first change that constrains the payload as a **set** rather than
file-by-file. Negative-tested: reintroducing the mismatch fails with the exact
diagnostic. It only covers `port`; the general invariant ("no payload file
contradicts another") is not yet mechanised.

## 8. Additional promote candidates

- [ ] 🔴 **When repairing a broken delivery path, audit what the breakage was hiding** → **Promote to memory** (type: feedback)
  > **Why**: `clarinet init --template` never worked from a pip install, so the template payload's `${VAR}` placeholders had never once been loaded by pydantic-settings. Making delivery work made every scaffolded project fail on first command — a blocker that only existed *because* the fix succeeded.
  > **How to apply**: before shipping a fix that makes a dead code path live, run the newly-reachable path end to end as a user would. "It was already there" is not evidence it works; it may be evidence nobody has ever executed it.

- [ ] 🔴 **A claim that appears in sibling files must be corrected across the set, in one pass** → **Promote to memory** (type: feedback)
  > **Why**: twice in this change I fixed a wrong statement in one file and wrote the same wrong statement into its sibling in the same commit — the SQLite claim, then the port. Reviewers caught both; five of six flagged the second independently.
  > **How to apply**: when correcting a factual claim (a default, a port, an engine, a path), grep the claim's *subject* across the whole artifact set before committing — not just the file you opened. For config payloads specifically, prefer a test asserting cross-file agreement over a careful read.

- [ ] 🟡 **Constrain config payloads as a set, not file-by-file** → **Promote to project CLAUDE.md** (scaffold/payload section)
  > **Why**: `settings.toml` and `settings.custom.toml` are merged by pydantic-settings with last-file-wins, so any disagreement between them is a live defect, not a style issue. Three consecutive blockers came from this.
  > **How to apply**: any new key added to one payload TOML gets checked against the other and against `env.example` in the same change; extend `test_payload_settings_agree_on_port` when the coupled keys are more than `port`.

- [ ] 🟡 **Derive error remediation from the error, not from probing the world** → **Promote to memory** (type: feedback)
  > **Why**: `_config_startup_error` guessed the remediation by stat-ing `e.path`, so it mislabelled a config root that is a regular file, and every `schema_bundler` failure, as "fix the import error". `ConfigLoadError.kind` already existed and was unused — and review of PR #352 had asked for exactly this fix once before.
  > **How to apply**: when a handler branches on filesystem state to decide what to tell the user, check whether the exception already carries the discriminator. If a past review asked for the structural fix, do it rather than refining the probe.
