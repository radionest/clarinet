# Overlap-coefficient ROI matching — design

**Date:** 2026-06-20
**Status:** approved (design), pending implementation
**Repos:** `clarinet` (framework) + `nir_liver` (consumer, `/home/nest/clarinet-stand/nir_liver`)

## Problem

`nir_liver` compares a doctor's segmentation against the master-model projection to
classify lesions as false-negative (missed) or false-positive (extra). Matching is
done per-ROI by voxel overlap. The intended rule is "two lesions are the same when
they overlap by at least 5%", but the current behaviour is wrong in two ways:

1. The 5% threshold never fires (see "Key finding" below).
2. The ratio, even when active, is computed relative to one side only, so a small
   lesion almost entirely inside a large one (or vice versa) is mis-classified once
   the two sizes differ a lot.

The correct metric is the **overlap coefficient (Szymkiewicz–Simpson)**:

```
coef = intersection / min(|A|, |B|)
```

which is identical to `max(overlap/|A|, overlap/|B|)` for a single ROI pair —
symmetric, and robust to size differences.

## Key finding — the 5% ratio is currently inert (F1)

`Segmentation.difference(other, *, max_overlap=0, max_overlap_ratio=None, ...)`:

```python
if overlap <= max_overlap:                       # max_overlap=0 -> only overlap == 0
    if max_overlap_ratio is not None:
        if overlap / total > max_overlap_ratio:  # at overlap==0: 0 > 0.05 -> never
            continue
    output ... = keep
```

`nir_liver` calls `proj.difference(seg, max_overlap_ratio=0.05)` **without**
`max_overlap`, so `max_overlap=0`. The outer gate then only admits regions with
`overlap == 0`, and the ratio branch is dead code. The effective rule today is
"a lesion is matched if it overlaps by >= 1 voxel" — `0.05` has no effect.

The unit test `test_difference_with_ratio` masks this by explicitly passing
`max_overlap=10` to open the absolute gate. The Slicer-side `subtract_segmentations`
is called without `max_overlap_ratio` in both `nir_liver` scripts, so it too runs at
"1 voxel = matched". The two code paths are therefore *accidentally consistent* on a
lenient threshold that nobody intended.

## Decision summary

| Decision | Choice |
|---|---|
| Scope | Framework, **both** matching paths: `Segmentation.difference` and Slicer `subtract_segmentations`, plus the `nir_liver` call-sites |
| API shape | **New mode flag** `ratio_mode: Literal["self", "min"] = "self"` on both methods. Default `"self"` preserves current behaviour exactly |
| Slicer scripts | **Both** `second_review.py` and `update_master_model.py` switch to `ratio_mode="min", max_overlap_ratio=0.05` |

Rationale for the mode flag over repurposing `max_overlap_ratio`: existing callers
and tests of the per-self ratio stay untouched (default `"self"`), and the new
semantics is explicit and self-documenting. F1 is resolved for `nir_liver` by moving
its calls to `"min"`, not by changing `"self"`.

## Semantics — `ratio_mode="min"`

For the iterated region `A` (a label on `self`) against the labels `B` on the other
side:

- **Per-pair**: `inter(A, B)` = number of `A`'s voxels falling inside `B`;
  `coef = inter / min(|A|, |B|)`.
- **Sizes are voxel counts**, not physical `regionprops.area`. `|B|` from
  `np.unique(other.img, return_counts=True)`; `|A|` from `np.sum(self.img == label)`.
  Using physical area would mismatch units against the voxel-count `inter` and break
  the ratio under non-unit spacing.
- **Matched** ⇔ `∃ B: inter(A, B) > max_overlap AND coef > max_overlap_ratio`.
  Boundary is `>` (consistent with the existing `"self"` code).
- **Multi-overlap**: when `A` touches several `B`, it is matched if *any* pair clears
  both thresholds (effectively the max `coef` over `B`, gated by the noise floor).
- `difference` keeps the **non**-matched regions of `self`; `subtract_segmentations`
  removes the matched ones. Both methods compute matching identically in `"min"`
  mode → single source of truth.
- `max_overlap` changes role: in `"min"` mode it is an independent per-pair noise
  floor combined with **AND**, not an outer gate that suppresses the ratio. This is
  the same AND-structure `subtract_segmentations` already uses
  (`remove = overlap>max AND ratio>max_ratio`).
- **`other` must be labeled** in `"min"` mode (per-`B` sizes are meaningful). In
  `nir_liver`: `proj` carries master-model labels, `seg` is autolabelled into
  connected components — both fine. A binary `other` (single label) degenerates `|B|`
  to the whole foreground; documented in the docstring.

`"self"` mode is left bit-for-bit unchanged (and the two methods' existing `"self"`
combination logic already differs — that pre-existing quirk is out of scope).

Defaults for `nir_liver`: `max_overlap=0` (any non-zero touch clears the floor),
`max_overlap_ratio=0.05`.

## Affected call-sites

### Framework (clarinet)
- `clarinet/services/image/segmentation.py::difference` — add `ratio_mode`, implement
  the `"min"` branch (per-`B` breakdown via `np.unique` on `other.img[coords]`,
  per-`B` voxel sizes precomputed once).
- `clarinet/services/slicer/helper.py::subtract_segmentations` — add `ratio_mode`,
  implement the `"min"` branch. Today `arr_b` is tested only as `> 0` (binary); the
  `"min"` branch needs per-label `B` sizes from `arr_b`
  (`ExportAllSegmentsToLabelmapNode` already lays segments out as labels `1..n`).
  Extract the per-label decision into a module-level **pure numpy** function
  (`_min_mode_*`) so it is unit-testable without Slicer (helper.py imports under the
  `_Dummy` fallback). helper.py runs *inside* Slicer and cannot import
  `clarinet.services.image`, so the math is necessarily duplicated.
- `symmetric_difference` — **no** `ratio_mode` passthrough (YAGNI; no consumer).

### nir_liver — core fix
- `plan/workflows/tasks.py:523,527` (`compare_w_projection`) — add `ratio_mode="min"`
  to both `difference` calls (`max_overlap_ratio=0.05` already present). This is what
  actually activates the 5% rule.

### nir_liver — Slicer scripts (both switch)
- `plan/scripts/second_review.py:25` —
  `subtract_segmentations(projection, doctor_seg, "MissedLesions", ratio_mode="min", max_overlap_ratio=0.05)`.
  Result ("missed") feeds the reviewer's Classification `_pool`; mirrors the pipeline FN.
- `plan/scripts/update_master_model.py:77` —
  `subtract_segmentations(doctor_islands, projection, output_name="_FP_tmp", ratio_mode="min", max_overlap_ratio=0.05)`.
  Result is renamed `NEW_*` and copied into projection + master_seg → **persistent
  master-model growth**. Consequence (accepted): borderline doctor lesions that
  overlap an existing master lesion by `coef < 5%` are now treated as FP and added,
  where the old "1 voxel = matched" rule suppressed them.

## Testing

- `tests/test_image.py` (pure numpy, primary coverage of the `"min"` math):
  - asymmetry both directions (small ROI ~fully inside a large one): `"self"` keeps
    it, `"min"` matches it;
  - multi-overlap (`A` spanning `B1`, `B2`): matched if any pair clears thresholds;
  - noise floor (high `coef` but `inter <= max_overlap` → not matched);
  - non-unit spacing → `coef` uses voxel counts (regression guard for the units bug);
  - regression: `test_difference_with_ratio` stays green (default `"self"` untouched).
- `clarinet/services/slicer/helper.py` + `tests/integration/test_slicer_helper.py`:
  unit-test the extracted `_min_mode_*` pure function (Slicer-free); add one `"min"`
  case to the existing integration test.
- nir_liver: `tests/workflow/test_workflow.py:84-85` (`false_negative_num==0`/
  `false_positive_num==0`) is expected to stay green (stand builds well-overlapped
  lesions, `coef≈1`); add a focused asymmetry test for `compare_w_projection`
  (small-inside-large). `test_lesion_accumulation.py` does not exist yet — create it
  or extend `tests/workflow/`.

## Rollout

- **No DB migration** — data shape is unchanged (`false_negative` list + counts).
- **Two repos, ordered**: the new `ratio_mode` param must exist in clarinet before
  `nir_liver` can call it. `nir_liver/pyproject.toml` declares no clarinet dependency,
  so clarinet is wired in another way (shared venv / PYTHONPATH / wheel) — confirm the
  install mode; it decides whether a clarinet release is needed between the two PRs or
  an editable install suffices. At least 2 PRs (clarinet framework → nir_liver
  call-sites).
- **Recompute existing patients** (operator action, out of code scope): existing
  `compare-with-projection` records hold FN/FP under the old "1 voxel" rule.
  Re-trigger compare per series (invalidate → `pending` re-runs `compare_w_projection`).
  Expect **more FNs** (5%-coef is stricter), and the existing "invalidate second-review
  on new FN" logic will fire a wave of re-openings — flag this to the operator.
- **Versioning**: clarinet — feature (minor bump; new public parameter).

## Non-goals

- Reconciling the pre-existing `"self"`-mode combination-logic difference between
  `difference` (keep iff `overlap<=max AND ratio<=max`) and `subtract_segmentations`
  (remove iff `overlap>max AND ratio>max`).
- Dice/IoU or any metric other than the overlap coefficient.
- `symmetric_difference` / `__sub__` behaviour.
