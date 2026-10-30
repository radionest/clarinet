# Retrospective — canonicalize-segmentation-grids

## §0 Evidence
- **Commits:** 14 (`c892496`..`50e01e6`). **Diff:** 24 files, +3497 / −183.
- **Areas:** `clarinet/services/image` (9 files), `tests` (6), `docs` (2) + `clarinet/docs` (2), `clarinet/services/slicer` (2), `.claude/rules` (1), `CHANGELOG.md` (1), `tests/integration` (1).
- **Tasks:** 23/23 (100%). **Two releases:** A (grid core + reader hardening + conform gate + Slicer conform-export), B (converter det>0 epoch).
- **Subagent dispatches:** ~26 — 10 implementers + 11 reviews (10 per-task + 1 final whole-branch) + 4 fix subagents + 1 consumer audit. Models: implementers sonnet (per user directive "write with sonnet"); reviews/audits/live-tests opus ("audit, propose and test w opus"); live-Slicer test tasks (6, 9) implemented on opus.
- **New external deps:** 0 (numpy/nibabel/pynrrd/SimpleITK already present).
- **Post-merge bugs:** N/A (not merged).
- **OpenSpec validate (verify time):** 5 passed, 0 failed. Delta specs unsynced (expected pre-archive).
- **Test signal:** `tests/test_grid.py` NEW; `test_image`/`test_orientation`/`test_slicer_helper_unit` expanded; +2 live-Slicer integration tests. Final gates: **232 unit passed / 0 failed**, **2 live-Slicer passed** (not skipped), `make check` clean.
- **Commit chain:** Grid VO → read_grid/disk-assert → reader hardening → conform gate+4D → Slicer conform-export → (post-write fail-closed fix) → live conform gate (+harden) → in-plane IOP order → det>0 slice sense → live no-conform gate → docs → stale-space fix.

## §1 Wins
- **One engine, both runtimes.** `grid.py` (numpy+stdlib only, zero clarinet imports) rides the correspondence bundle into Slicer (`5d71961`, `8c42557`), so the clarinet process and the in-Slicer helper classify grids from ONE source — no verdict drift (the exact failure PR #494 eliminated for set-ops). Enforced by the bundle-exec test.
- **The geometry-preservation invariant anchored the converter epoch.** `test_epoch_change_is_lossless_index_rearrangement` (`ce289fe`) asserts REARRANGED + identical physical bbox (1e-9) + zero voxel drift via `reindex_to(order=0)` + `assert_array_equal`. The Task 7 review verified it's non-circular (asymmetric fixture + random content). This is what let the breaking epoch ship confidently.
- **Live "test w opus" disproved a plausible static suggestion.** The Task 5 review recommended propagating `GetLabelValue()` in the REARRANGED rebuild; the Task 6 opus live-test empirically found it REGRESSES overlapping segments (collapses 2 layers→1) on Slicer 5.10 and left helper.py unchanged, pinning `conf==plain` as a regression guard (`699080a`). Static review would have shipped the wrong fix.
- **The consumer audit found ZERO framework breakage.** Because Task 7 kept array/direction/spacing/affine mutually consistent, every geometry construction reads from the affine — 0 BROKEN across 12 consumers (opus audit, `task-8-audit.md`). The breaking epoch has no framework blast radius.
- **Fail-closed export completed.** The Task 5 review found (and a fix closed, `92fdb3f`) that a post-write re-read EXCEPTION (not just a mismatch) would leave a bad artifact — closing the "no bad artifact survives" contract.

## §2 Misses
- 🟡 **Task 2 tautological contrast test.** The "catches what the in-memory guard misses" test compared a seg to its own copy (trivially True). Caught by the Task 2 review, fixed to run `same_grid` on the pair that matters (`2f1fa6e`).
- 🟡 **Stale NRRD `space` label on BOTH write paths** — a whole-branch-only finding. `_save_nrrd` and `_conform_layered_seg` wrote LPS numbers while inheriting a stale foreign `space` string; a space-honoring reader would double-flip. The per-task reviews structurally couldn't see it (split across two functions touched by different tasks); the final whole-branch review found it (`50e01e6`).
- 📌 **Controller prep over-prediction.** My Task 7/8 ledger note claimed `TestGroundTruthSliceGeometry` needed updating; it is axis-order-agnostic and needed none — the implementer corrected it (self-corrected in ledger).
- 📌 **grid.py docstrings tripped the no-clarinet-token bundle test** when Task 5 bundled the module (prose said "clarinet"); reworded to "framework" (`8c42557`).

## §3 Plan deviations
- **Task 4 — `from_layers` → raw-header-preservation.** plan.md Task 4 Step 2 said "reassemble `from_layers`", but `from_layers` forces `LabelValue=1`/one-seg-per-layer and cannot preserve non-1 labels — contradicting the spec's own metadata-preservation requirement. Resolved in favor of the binding spec via raw-header preservation (`4314e5f`). **Open: plan.md correction owed to a Fable subagent (FABLE-1 in the ledger); design.md D7 + tasks.md 1.4 say "reassembled … preserved" without naming a method, so only plan.md is wrong.**
- **Release tags deferred to post-merge.** plan Tasks 6/9 said "+ tag"; git tags trigger release CI and are outward-facing post-merge steps, so only the CHANGELOG `## Unreleased` entries were written (consolidated in Task 10); tagging is the user's post-merge action.
- **`docs/image-service.md` staleness fixes** (Task 10) beyond the literal "add a pointer": its canonicalization section described the pre-D6 +dominant rule and its `conform_seg_to_grid` row predated the relation gate. Rewritten to shipped behavior (reviewer confirmed correct + warranted).

## §4 Skill / workflow compliance
- **superpowers:using-git-worktrees** ✓ — native `EnterWorktree` (Step 1a), openspec store symlinked.
- **superpowers:subagent-driven-development** ✓ — fresh implementer per task + per-task review + final whole-branch review; ledger-tracked; foreground dispatch adopted after Task 1 to avoid Stop-hook cycling.
- **superpowers:test-driven-development** ✓ — every implementation task RED→GREEN (implementers reported failing-first evidence).
- **superpowers:requesting-code-review** ✓ — 10 per-task reviews + 1 final whole-branch review, all opus.
- **superpowers:finishing-a-development-branch** — PENDING (next step; PR gated on `pr-diff-reviewer`).
- **openspec-verify-change** ✗ unavailable — fell back to the 8 manual verify checks (recorded in verify.md), as the instruction sanctions.

### Deliberately Skipped Skills
None skipped by choice; the only ✗ (openspec-verify-change) was unavailable in the environment and its manual fallback was executed in full.

## §5 Surprises
- **GDCM already sorts slices ascending along the IOP normal**, so the D6 canonical-sense flip is a **no-op for the vast majority of real conversions** — the visible epoch change is almost always just the in-plane transpose (Task 8 finding; folded into `grid-workflows.md` traps).
- **Slicer 5.10 cannot hold 2 overlapping layers + distinct non-1 label values simultaneously** — `SetLabelValue` collapses the overlap (Task 6 live discovery; drove the Minor 2 no-fix decision).
- **`_save_nrrd`'s `space` emission was pre-existing** (introduced 2026-03-08), so the new missing-`space`→raise is a NARROW compat break (only pre-March-2026 or third-party space-less NRRDs), not a broad one (Task 3 finding, confirmed by the reviewer via `git log -S`).

## §6 Promote candidates → long-term learning
- [ ] 🟡 **A final whole-branch review catches cross-file bugs per-task diff-scoped reviews cannot.**
      → **Promote to** memory (feedback)
      > **Why**: the stale-`space` bug lived in two functions (`_save_nrrd`, `_conform_layered_seg`) touched by different tasks; each per-task review saw only its own diff and passed. Only the whole-branch review, reading both together, saw the shared contract violation.
      > **How to apply**: in subagent-driven-development, always run the final whole-branch review before PR even when every per-task review is green — it is the only gate that sees cross-task/cross-file contracts.
- [ ] 🟡 **For Slicer/runtime-behavior fixes, validate against live before applying a static-review suggestion.**
      → **Promote to** memory (feedback)
      > **Why**: the plausible `GetLabelValue()` propagation fix (from a static opus review) regressed overlapping segments on live Slicer 5.10 — the runtime's per-layer labelmap semantics aren't derivable from the code alone.
      > **How to apply**: when a review suggests a fix to in-Slicer helper mechanics, route it through the live-Slicer test task to empirically confirm/refute before landing it.
- [ ] 📌 **`LayeredSegmentation.from_layers` normalizes to label=1/one-seg-per-layer — do not use it for metadata-preserving 4-D repair.**
      → **Promote to** one-off (already captured in `grid-workflows.md` + code comments)
      > **Why**: it silently drops non-1 LabelValues and multi-seg-per-layer structure.
      > **How to apply**: for exact 4-D `.seg.nrrd` repair, rearrange the raw `(L,X,Y,Z)` array + re-apply original `Segment*_` headers on the reference grid.
