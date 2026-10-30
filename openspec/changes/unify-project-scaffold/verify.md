# Verification Report

**Change**: `unify-project-scaffold`
**Verified at**: `2026-07-27` (re-run at HEAD `b723f5c`)
**Verifier**: Claude (apply session, inline verification per schema fallback)

---

## 1. Structural Validation (`openspec validate --all --json`)

- [x] All items return `"valid": true`

```text
total items: 6  invalid: 0
  change add-scripting-frame            -> True
  change canonicalize-segmentation-grids -> True
  change slicer-engine-parity           -> True
  spec   slicer-overlap-detection       -> True
  change unify-project-scaffold         -> True
  change unique-within-constraints      -> True
```

| Item | Type | Issues |
|---|---|---|
| — | — | none |

---

## 2. Task Completion (`tasks.md`)

- [x] 42 of 44 tasks `- [x]`; the 2 open rows are deliberate, documented in place

| Task | Reason not done | Blocks archive |
|---|---|---|
| 8.4 Bump version to `0.11.0` | This repo cuts releases by tagging a standalone `chore:` version-bump commit (`.claude/rules/release.md`), and the CHANGELOG accumulates under `## Unreleased`. Bumping inside a feature PR pre-empts the release decision and would collide with the other open breaking PR (#497). Entries were added under `## Unreleased` instead, and the startup diagnostic was made version-agnostic so it cannot go stale. | No |
| 8.5 Stop shipping `examples/demo/.claude/settings.local.json` in the sdist | Explicitly optional in the plan and unrelated to #472. User confirmed out of scope. | No |

---

## 3. Delta Spec Sync State

| Capability | Sync state | Note |
|---|---|---|
| `project-scaffolding` | ✗ Needs sync | New capability; no `openspec/specs/project-scaffolding/` yet |
| `agent-doc-delivery` | ✗ Needs sync | New capability |
| `plan-directory-convention` | ✗ Needs sync | New capability |

`openspec/specs/` currently holds only `slicer-overlap-detection`. In this
deployment `openspec archive` runs from the main checkout **after** the PR
merges, so "✗ Needs sync" is the expected state at verify time. **Non-blocking.**

---

## 4. Design / Specs Coherence Spot Check

| Sample | design.md | specs | Gap |
|---|---|---|---|
| Payload location | D1 — payload moves to `clarinet/scaffold/`, resolved off `clarinet.__file__`, force-shipped via hatch `artifacts` | `project-scaffolding` — "payload MUST resolve inside the package" | None. Verified against a built wheel: 12 payload files present, `examples/` 0 entries |
| Single init path | D2 — retire the template system | `project-scaffolding` — "`init` MUST be a single path" | None |
| Seed reclassification | D4/D5 — `overview.md` becomes a write-once seed; `update` migrates rather than deletes | `agent-doc-delivery` — 4 requirements incl. "migrated, not deleted" | None |
| Dotfile storage | D6 — stored undotted, renamed at scaffold time | `project-scaffolding` — "dotfiles MUST be materialized dotted" | None |
| Convention flip | D7 — default to `./plan/`; the diagnostic is load-bearing, not cosmetic | `plan-directory-convention` — 3 requirements (default, abort, upgrade-aware) | None |
| No root `scripts/` | D11 — scaffold does not create it | — | **Warning (non-blocking):** a deliberate *negative* decision with no corresponding requirement. Correct as-is: specs state what the system does, and "does not create a directory" needs no requirement. Recorded so a later reader does not mistake it for an omission |
| Review corrections | D12 — four defects found by pre-PR review | — | None. All four were implementation failing to meet requirements already written (mode-independent abort, provenance-based hint, re-runnable init, prune in every mode). **No spec text changed** — the specs were right and the code was wrong, which is the desired direction |

**Drift warnings (non-blocking):** one, D11 above.

---

## 5. Implementation Signal

- [x] No unstaged files in the worktree
- [x] All commits pushed (`HEAD` == `origin/worktree-unify-scaffold` == `acd6680`)

**Commit range**: `d0df3c6..b723f5c` (12 commits)

```text
acd6680 fix: address pre-PR review — re-runnable init, prune in every mode, TOML abort
88a5aff docs: document the single-path init, the seed CLAUDE.md and the ./plan/ default
455ec30 feat(config)!: default config_tasks_path to ./plan/ and abort on a missing root
7b8d955 refactor: drop the duplicated agent-doc copy from the project template
f001750 feat(cli)!: single-path clarinet init, retire --template
c333671 feat(agent): make overview.md a project-owned seed and prune stale managed docs
134dd1f feat(cli): move the project scaffold payload inside the package
2fcd8e0 docs(agent): backport unique_by, {parent_id} and allow_path_collision
```

**Test evidence**

| Check | Result |
|---|---|
| `make test-fast` (full suite) | 3135 passed, 0 failed, 204 skipped |
| Gate `-k "agent_scaffold or scaffold or config_loading or cli_init"` | 122 passed (pre-change baseline for the original selector: 100) |
| `make check` (ruff format + check, mypy) | clean, 231 source files |
| Wheel build | 12 files under `clarinet/scaffold/`, `examples/` 0 entries, no `cli/templates.py` |
| CLI smoke — `init` twice | exit 0 both runs, edited seed preserved |
| CLI smoke — legacy layout → `agent init --force` → `agent update` | legacy `overview.md` removed, user edits survived in `.claude/CLAUDE.md` |

---

## 6. Front-Door Routing Leak Detector (warning, non-blocking)

- [x] No files under `docs/superpowers/specs/`

| File | Captured in change? | Action |
|---|---|---|
| — | — | none needed |

---

## 7. Deferred Manual Dogfood vs Automated Test Equivalence

`plan.md` contains **0** `[~]` deferred rows — section intentionally blank per
the schema's rule ("plan.md has no `[~]` rows → blank is PASS").

For the record, the two manual smoke checks that *were* run have automated
equivalents, so nothing rests on manual verification alone:

| Manual check | Equivalent automated test | Coverage | Real gap? |
|---|---|---|---|
| `clarinet init` twice | `tests/test_cli_init.py::test_init_is_rerunnable`, `::test_rerun_keeps_the_edited_seed` | Both runs complete; payload + managed docs present; edited seed byte-preserved | ❌ covered |
| legacy → `agent init --force` → `update` | `tests/test_agent_scaffold.py::test_forced_init_migrates_legacy_overview_instead_of_stranding_it` | Legacy doc removed, edits migrated, header stripped | ❌ covered |
| wheel contains the payload | `tests/test_project_scaffold.py::test_scaffold_root_is_inside_the_package`, `::test_payload_manifest_complete` | Structural guard (path inside package + manifest); the wheel build itself is not automated — see note | ⚠️ partial |

> Note on the wheel: D10 deliberately chose structural guards over a
> build-a-wheel test (slow, needs the frontend toolchain). The two guards fail
> on the pre-change code and catch the D6 failure class. The one thing they
> cannot catch is a `pyproject.toml` `artifacts` regression; the release
> workflow already verifies wheel contents for `clarinet/static/`, and
> extending that check to `clarinet/scaffold/` is a sensible follow-up.

---

## 8. Unreconciled Ledger Theories (BLOCKING)

`.superpowers/sdd/` does not exist in this worktree — the plan was executed
inline in the apply session rather than via subagent-driven-development, so
there is no ledger to grep. **N/A.**

| Hit (file:line) | Claim | Resolution |
|---|---|---|
| — | — | N/A |

No unverified "pre-existing failure" claims were carried: the suite was green
at every task boundary, and the only failures encountered (13 reconciler tests,
6 lifespan tests) were reproduced, root-caused to this change's new
precondition, and fixed in the same cycle — not labelled pre-existing.

---

## Overall Decision

- [ ] ✅ PASS
- [x] ⚠️ PASS WITH WARNINGS
- [ ] ❌ FAIL

**Warnings** (none blocking archive):

1. Delta specs for all 3 capabilities await `openspec archive`, which runs from
   the main checkout after the PR merges. Expected in this deployment.
2. Two `tasks.md` rows intentionally left open (§2), both documented in place.
3. D11 is a negative decision with no matching requirement (§4) — correct, but
   recorded so it is not misread as a coverage hole.
4. Wheel-content protection is structural, not a build test (§7).

**Four review rounds — outcome (not a verify check, but material):**

| Round | HEAD | Verdict | Blocker(s) |
|---|---|---|---|
| 1 | `88a5aff` | BLOCK | `init` not re-runnable; `init --force` stranded a legacy overview so a later `update` deleted user edits |
| 2 | `acd6680` | BLOCK | every scaffolded project dead on arrival — `${VAR}` placeholders reaching `int` fields |
| 3 | `96d1b8d` | BLOCK | payload header claimed SQLite while `settings.toml` pins Postgres |
| 4 | `9a7bde3` | BLOCK | payload files disagreed on `port` (8000 vs 8111) |

All four are fixed and verified. Rounds 2–4 were **one recurring class** — a
payload file contradicting another payload file — and each was introduced by
the fix for the previous one. `b723f5c` adds the first structural defence:
`test_payload_settings_agree_on_port`, negative-tested against the
reintroduced mismatch.

The user reviewed this history and elected to cap the rounds and open the PR
with `SKIP_PR_REVIEW=1` rather than run a fifth pass. Recorded here because the
new consistency guard has itself never been through review.

Out-of-scope defects found and filed, not fixed here: issue #503
(`CLARINET_DATABASE_URL` is a silent no-op in docs and a shipped error message).
The reviewer also identified a follow-up worth filing: the config startup banner
mislabels every `schema_bundler` failure as an import error — now partly
addressed by keying the hint off `ConfigLoadError.kind`, but the path-less
raises remain.

**Superseded:**

The pre-PR review returned **BLOCK** on commit `88a5aff` and wrote no marker
file. All findings were verified and addressed in `acd6680` — two blockers
(re-runnable `init`; prune-in-every-mode to stop a data-loss path), two
spec-compliance defects (provenance-based hint; TOML-mode abort), seven stale
doc references, and one out-of-scope payload defect (`database_login` →
`database_username`). Because those fixes are substantive, `pr-diff-reviewer`
must be re-run before `gh pr create` — the hook gate is not satisfied.

**Next step**: `gh pr create`. Archive only after the PR merges.
