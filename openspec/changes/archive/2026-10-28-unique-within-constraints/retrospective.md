# Retrospective: unique-within-constraints

> Written: 2026-07-21 (after verify passed)
> Commit range: `15465a5..cc80faf`
> Worktree: /home/nest/Projects/clarinet/.claude/worktrees/unique-within-constraints

---

## 0. Evidence

- **Commit range**: `15465a5..cc80faf` (18 commits)
- **Diff size**: +2755 / −655 lines across 57 files
- **Tasks done**: 42/42
- **Active hours**: ~6h single session (sum of subagent wall-clock; fully sequential per SDD)
- **Subagent dispatches**: 27 fresh (12 implementers, 3 fixers, 12 reviewers) + 2 SendMessage re-review continuations
- **New external dependencies**: none
- **Bugs encountered post-merge**: n/a (pre-merge; 1 Critical caught by final whole-branch review, fixed in `cc80faf`)
- **OpenSpec validate state at verify**: pass (all items valid)
- **Test coverage signal**: `make test-fast` 3023 passed / 0 failed / 152 skipped (main baseline 2910 unit); frontend 231 passed; new test files: `test_uniqueness_partitions.py` (8), `test_path_uniqueness.py` (10+2), `tests/integration/test_unique_by.py` (13)

Commit chain (時序):

```
27101ff feat(models): unique_by partition canonicalization primitive
115dbb7 feat(files): {parent_id} placeholder resolved from FK column
3f334ea feat(models): unique_by column, legacy translation, link opt-out, ON DELETE CASCADE
c6c3fb9 feat(config): RecordDef.unique_by, FileRef opt-out, loader forwarding
ec04580 test(conftest): fixture engines use app json_serializer for flush parity
77bc2ae feat(repo): partition-aware unique_by enforcement across create/claim/pool
873043a feat(config): output-path uniqueness validator + per-file opt-out + PATCH guard
7797ce5 fix(config): reconcile unique_by canonically (raw-list rows, explicit None honored)
4c4903f feat(config): TOML unique_by export (false = off) + legacy key compat
ce64aff test(config): cover TOML export of a raw DB-row unique_by (JSON list shape)
e31eaa6 feat(db): enforce foreign keys on SQLite + startup FK audit
ec1d421 feat(frontend): unique_by replaces unique_per_user in models and edit page
9c83d28 docs: unique_by, {parent_id}, path-uniqueness, CASCADE trade
72256c5 docs: finish unique_per_user -> unique_by rename in api-urls + models note
aa8b6cb refactor: finish unique_per_user -> unique_by rename in inline docs
74e25b0 fix(test): mock/stub records need parent_record_id for {parent_id} resolution
5baf25f fix(test): stale unique_per_user kwarg no-ops on direct RecordType construction
cc80faf fix(repo): exclude the record under evaluation from assignment-time unique_by check
```

---

## 1. Wins

- [evidence: §0 tasks 42/42, most commits match plan wording] The 12-task plan with inline code survived contact almost verbatim — 8 of 12 implementers were near-pure transcription+testing, which is what made sonnet-tier implementers viable.
- [evidence: Task 6 review, `path_uniqueness.py:94`] Per-task opus reviews caught real defects at the gate: the plan's own `fd.role != "OUTPUT"` literal would have made the validator a silent no-op (`FileRole.OUTPUT == "output"`); the Task 3 reviewer independently derived the CASE-backfill trap; the Task 7 reviewer verified the frozenset flush path end-to-end.
- [evidence: `cc80faf`, final review] The final whole-branch review earned its cost: the Critical (parent-only records listed as claimable but 409 on every claim — `ensure_unique_by` self-collision) was invisible to every per-task review because it spans Task 5's two halves (pool filter buckets exclude parent-only types; assignment re-check counts the candidate row itself).
- [evidence: `ec04580`] Root-cause discipline held under pressure: the frozenset-flush failures were traced to fixture engines lacking the app `json_serializer` (production never broken) instead of patching `PortableJSON`.
- [evidence: Tasks 3-5 reports] The sanctioned SKIP=mypy window (Tasks 3-4, exactly 3 stale references) closed on schedule at Task 5 with repo-wide mypy green — planned transitional breakage, not drift.

## 2. Misses

- 🔴 [blocking | evidence: Task 12 report, `74e25b0`+`5baf25f`] "Pre-existing failure" claims were only ever verified relative to each task's OWN diff (git stash), never against MAIN — so 21 real branch regressions (15× test doubles missing `parent_record_id` after Task 2; 6× legacy-kwarg no-ops on table=True fixtures after Task 3) traveled through six task gates labeled "pre-existing" until Task 12 reconciled against `15465a5` and refuted the theory.
- 🟡 [painful | evidence: ledger Task 6 note vs Task 12] The controller ledger recorded the "environment-dependent" hypothesis as if it were fact; downstream dispatches inherited it as context. Theories must be labeled as theories in the ledger.
- 🟡 [painful | evidence: ledger 4-vs-10 correction] Transitional-failure counts drifted between tasks (4 vs 10) because each task measured with a different `-k` filter — no canonical failing-set snapshot existed until Task 12.
- 📌 [nit | evidence: Task 11 report] The plan's verification selector `-k "golden or docs_sync"` matched zero tests; the real guard is `test_deep_docs_identical_to_rules_seeds`. Plan-time selectors were not validated against the suite.
- 📌 [nit | evidence: Task 5/6 briefs] Plan line numbers drifted by the time tasks ran; "locate by symbol" instructions absorbed it, but briefs citing lines invite false anchoring.

## 3. Plan deviations

| Plan task | What changed | Why |
|-----------|--------------|-----|
| 4 | Also swapped `reconciler._COMPARED_FIELDS` + python-mode tests (Task 7 territory) | Leaving the stale name would break the very forwarding Task 4 built; adjudicated justified-collateral |
| 6 | `fd.role != FileRole.OUTPUT` (not the plan's `"OUTPUT"` literal); factories use `model_construct`; +2 coarser-level tests; PATCH explicit-None carve-out + invalid-token test folded in from Task 3 concerns | Plan literal was vacuous against the lowercase str-enum; constructor now validates so intentionally-bad instances need bypass; coarser-level rule had zero coverage in the plan's 8 tests |
| 10 | `unique_by` UI is a native select + dedicated PATCH (admin.gleam pattern), not a formosh form field | formosh enum/select widgets hardcode scalar `StringValue` — cannot emit `null`/array; verified against vendored source |
| 11 | project_template swap was a no-op (never used `unique_per_user`); api-urls.md fix added after review | Empirical state differed from plan assumption; review caught the API-surface staleness |
| 12 | Produced 3 fix commits instead of zero code changes | The reconciliation-vs-main it performed found the 21 regressions (see Misses 🔴) |
| post-plan | `ensure_unique_by` gained `exclude_record_id` (final fix wave, `cc80faf`) | Assignment-time self-collision — plan pseudocode was creation-framed and reused unmodified at assignment time |

## 4. Skill / workflow compliance

| Skill                                            | Used |
|--------------------------------------------------|------|
| superpowers:brainstorming                        | ✓ (pre-apply; brainstorm.md) |
| superpowers:writing-plans                        | ✓ (pre-apply; plan.md) |
| superpowers:using-git-worktrees                  | ✓ (EnterWorktree native path) |
| superpowers:subagent-driven-development          | ✓ (27 dispatches, ledger, per-task reviews) |
| (transitive) superpowers:test-driven-development | ✓ (every implementer reported RED→GREEN evidence) |
| (transitive) superpowers:requesting-code-review  | ✓ (11 per-task reviews + final whole-branch on opus) |
| superpowers:finishing-a-development-branch       | ✓ (in progress — next step this cycle, after this retro per schema ordering) |

### Deliberately Skipped Skills

(none — all rows ✓)

## 5. Surprises

- The plan asserted the `/types/{name}` PATCH path "already" honors `model_fields_set` — false: only `edit_window_days` had the carve-out; explicit `{"unique_by": null}` was silently dropped until Task 6 extended it.
- `FileRole.OUTPUT` is lowercase `"output"` — the plan's uppercase literal comparison would have shipped a no-op validator.
- The proposal's "zero current violations across 55 RecordDefs" scan did not survive the final predicate: 3 demo singletons violated it (their safety under old semantics was the same accident the proposal itself documented).
- formosh selects physically cannot produce `null`/array values (hardcoded `StringValue`).
- Test fixture engines lacked the app's `json_serializer` — a test-infra parity gap that masqueraded as a production frozenset-flush bug.
- Reusing the creation-time uniqueness count at assignment time self-collides: the candidate row already exists and matches its own partition tuple. The deepest surprise, found only by the whole-branch review.

## 6. Promote candidates → long-term learning

- [x] 🔴 **Reconcile "pre-existing" test failures against MAIN at first observation, not at the end** → **Promote to memory** (type: feedback)
  > **Why**: 21 branch regressions traveled through six SDD task gates labeled "pre-existing" because each task only stash-verified against its own diff; the theory was refuted at Task 12.
  > **How to apply**: In any multi-task branch (SDD or otherwise), the first task that observes a failure it did not cause must classify it against the branch base (run the failing file on main / at the base SHA) or record it in the ledger as "UNRECONCILED THEORY" — the final verification task treats unreconciled entries as blockers.
  > **Promoted 2026-07-21**: ~/.claude/rules/unverified-claims.md + superpowers-bridge schema (apply step 2 executor directives, verify check 8); rule chosen over memory for cross-project reach

- [x] 🟡 **A uniqueness/quota predicate reused across pre-insert and post-insert contexts needs a self-exclusion audit** → **Promote to memory** (type: project)
  > **Why**: `ensure_unique_by` was written for creation (row absent) and reused at claim/assign (row present) — the candidate matched itself, producing listed-but-unclaimable records; only the whole-branch review caught it.
  > **How to apply**: Whenever a count/EXISTS constraint check is called with an already-persisted candidate row, demand an `exclude_record_id`-style parameter and a test asserting the idempotent case.
  > **Promoted 2026-07-21**: .claude/rules/record-repo.md §"Constraint predicates: pre-insert vs post-insert reuse" (this branch) + .claude/rules/pr-review.md P30 (main)

- [x] 🟡 **Ledger entries must label hypotheses as THEORY until verified** → **Promote to memory** (type: feedback)
  > **Why**: The controller wrote "claimed environment-dependent" into the ledger and later dispatches inherited it as established context; Task 12 refuted it.
  > **How to apply**: When recording a claim in a progress ledger that no one has verified (especially about test failures or external systems), prefix it with THEORY and name what evidence would confirm it.
  > **Promoted 2026-07-21**: ~/.claude/rules/unverified-claims.md + superpowers-bridge schema (apply step 2 executor directives)

- [x] 📌 **Validate plan-time pytest `-k` selectors against the suite while writing the plan** → **One-off** (plan-review checklist item)
  > **Why**: `-k "golden or docs_sync"` matched zero tests; harmless here because implementers found the real guard, but a selector that silently matches nothing can fake a green gate.
  > **How to apply**: During plan self-review, run each named selector with `--collect-only -q` and pin the matched count.
  > **Promoted 2026-07-21**: global CLAUDE.md §"Plans & specs" + superpowers-bridge plan instruction (encoded despite one-off classification)
