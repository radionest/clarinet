# Component correspondence & set-operation strategies — design

**Date:** 2026-06-26
**Status:** approved (design), pending implementation
**Supersedes:** `2026-06-20-overlap-coefficient-matching-design.md` — the narrow overlap-coefficient
fix. Its overlap-coefficient metric, the inert-threshold bug (F1), the Slicer
`subtract_segmentations` path and the `nir_liver` consumer/rollout are **absorbed** below.
**Repos:** `clarinet` (framework) + `nir_liver` (consumer, `/home/nest/clarinet-stand/nir_liver`)

## Problem

`Segmentation` (`clarinet/services/image/segmentation.py`) offers component-based set operations
(`intersection`, `union`, `difference`, `symmetric_difference`, `append`) over labeled masks. Every
one iterates the connected components of `self` and **collapses `other` into a binary mask** — so the
relationship *which component of A corresponds to which component of B, and how strongly* is never
represented. Concrete limitations:

- **1:N is unhandled.** When one component overlaps several on the other side there is no way to
  resolve "which is the real match" — e.g. keep/delete only the one with the larger overlap. `append`
  just raises `ValueError("ROI overlaps multiple labels")`.
- **`symmetric_difference` loses component identity.** Defined as
  `self.union(other).difference(self.intersection(other))`; `union` relabels into fresh connected
  components, so when an agreeing and a disagreeing piece are physically connected they merge into one
  blob and the whole blob is dropped.
- **The matching criterion is hard-wired** to absolute voxel overlap (`min_overlap`) or one-sided
  coverage (`min_overlap_ratio`). No IoU/Dice/overlap-coefficient/centroid option, and no way to add
  one without editing each operation.
- **Inert-threshold bug (inherited F1).** `difference(other, max_overlap=0, max_overlap_ratio=r)`
  gates `if overlap <= max_overlap:` *then* `if overlap/total > r: continue`. With `max_overlap=0`
  the outer gate admits only `overlap == 0`, so the ratio branch is dead code — the intended
  "match if overlap ≥ r" never fires. `nir_liver` relies on this
  (`proj.difference(seg, max_overlap_ratio=0.05)`), so its 5%-overlap lesion FN/FP rule silently
  degrades to "1 voxel = matched". The two-gate AND structure is the root cause.

clarinet is a framework: it should expose **pluggable strategies for both the correspondence
(matching) layer and the operations layer**, be open to new strategies without touching the core, and
make every strategy trivially unit-testable.

## Goals

- Separate **correspondence detection** from **set operations**, with a reusable matching result
  (`Correspondence`) between them.
- Pluggable, composable strategies on two axes (measure × matching policy) plus operations, expressed
  as `Protocol` + `frozen` dataclasses (composition, not inheritance; mypy/PEP 695 friendly).
- Each strategy is a **pure function over plain data** → unit-testable on three-line literals, no
  images.
- Reproduce all current *working* behavior by default (backward compatible). The only intentional
  behavioral change is a cleaner component-level `symmetric_difference`.
- Eliminate the inert-threshold bug **class** by moving tolerance into a single `min_score` on a
  single measure (no two-gate AND).
- Reuse the same pure math in the Slicer `subtract_segmentations` path, which cannot import the heavy
  image stack.

## Non-goals

- New external dependencies (scipy & skimage already present).
- Changing on-disk / DB data shapes.
- Zero-overlap centroid matching in v1 (data model accommodates it; `CentroidCandidates` is an
  extension point).
- A name→factory registry / config-string selection (YAGNI; add when config-driven selection is
  actually needed).
- Reconciling the pre-existing `"self"`-mode combination-logic difference between `difference` and
  `subtract_segmentations` beyond what the new single source of truth provides.

## Architecture — three pure stages

Testability comes from **plain data flowing between layers** (no `Segmentation`/numpy image needed to
construct a strategy's input in a test). Only two functions touch arrays
(`build_overlap_graph`, `render`); everything else is pure.

```
masks A,B ──[1] decompose──▶ Components (label, size, centroid)
Components ──[2] build graph─▶ OverlapGraph (edges carry RAW stats: |a∩b|, |a|, |b|, dist, containment)
OverlapGraph ─[3a] measure──▶ edge scores          ← Measure strategy           (pure fn)
scored graph ─[3b] match────▶ Correspondence        ← MatchingStrategy           (pure fn)
Correspondence ─[4] operate─▶ KeepPlan ─ render ─▶ output mask   ← SetOperation  (pure fn) + painter
```

New module `clarinet/services/image/correspondence.py` (pure-data layer + strategies). Operations may
live there or in `component_ops.py`. `segmentation.py` (already 592 lines) is **not** bloated:
`Segmentation` methods become thin adapters "decompose → `correspond()` → operation → `render()`".

`correspondence.py` must depend only on `numpy` (+ optional `scipy` for the Hungarian matcher) and
**must not import `image.py`/`segmentation.py`** — this keeps the pure core importable/vendorable by
the Slicer path (see below).

## Layer 1 — data model

All inter-layer types are `frozen` dataclasses of primitives:

```python
@dataclass(frozen=True)
class Component:
    label: int                    # value in the source labelmap (identity)
    size: int                     # voxel count |a|
    centroid: tuple[float, ...]   # center of mass, voxel-index units

@dataclass(frozen=True)
class PairStats:                  # one candidate edge a—b, raw quantities only
    a: int                        # A-side label
    b: int                        # B-side label
    inter: int                    # |a ∩ b|, voxels
    size_a: int
    size_b: int
    centroid_distance: float      # physical (mm), via spacing
    a_centroid_in_b: bool         # a's centroid falls inside b's voxels
    b_centroid_in_a: bool

@dataclass(frozen=True)
class OverlapGraph:
    components_a: tuple[Component, ...]
    components_b: tuple[Component, ...]
    edges: tuple[PairStats, ...]  # candidate pairs only (default: inter > 0)
    spacing: tuple[float, ...]

@dataclass(frozen=True)
class MatchGroup:
    a_labels: tuple[int, ...]     # A-side members of this correspondence group
    b_labels: tuple[int, ...]     # B-side members
    score: float                  # resolved measure score for the group

@dataclass(frozen=True)
class Correspondence:             # the reusable artifact — input to every operation
    matches: tuple[MatchGroup, ...]
    unmatched_a: tuple[int, ...]  # A-only component labels
    unmatched_b: tuple[int, ...]  # B-only component labels
```

Key decisions:

1. **Raw stats in the graph; measures are pure functions** of `PairStats` (IoU/Dice/coverage/centroid
   are derived, not stored) → a measure is tested as `iou(PairStats(inter=4, size_a=8, size_b=8, ...))`
   with no array.
2. **Centroid in two forms:** voxel-index in `Component` (for `centroid-in-mask`, deterministic);
   physical distance precomputed into `PairStats.centroid_distance` via `spacing`, so a measure stays
   a scalar function and never touches arrays/spacing.
3. **`MatchGroup` tuples express every topology** — 1:1, 1:N, N:1, N:M. An argmax winner = 1:1 groups
   with losers in `unmatched_*`. Topology is a *result of the policy*, not baked into the type.
4. **`Correspondence` is the single inter-layer artifact.** An operation consumes it without knowing
   which measure/policy produced it.

Candidate-edge generation (v1): edges are built for **overlapping pairs (`inter > 0`)** only — cheap,
covers the 1:N case. Zero-overlap centroid matching (two readers, shifted draw → `inter = 0`, centers
close) needs candidates without overlap; `PairStats` already accommodates it (`inter = 0`,
`centroid_distance` set). Generation is a future `CentroidCandidates(max_mm=d)` strategy, not v1.

## Layer 2 — strategy protocols + starter set

```python
class Measure(Protocol):
    def __call__(self, e: PairStats) -> float: ...      # higher = better match

class MatchingStrategy(Protocol):
    def __call__(self, graph: OverlapGraph) -> Correspondence: ...
```

Convention: **measure returns a score where higher = better**; the cut-off lives in the matcher
(`min_score`), not in the measure. So `centroid_distance` (smaller is better) is wrapped as
`CentroidProximity`, keeping all measures homogeneous. Uniform boundary: **matched iff `score ≥ min_score`**.

### Starter measures (pure functions of `PairStats`)

```python
@dataclass(frozen=True)
class AbsoluteOverlap:               # |a∩b| voxels — current min_overlap
    def __call__(self, e): return float(e.inter)

@dataclass(frozen=True)
class IoU:
    def __call__(self, e):
        u = e.size_a + e.size_b - e.inter
        return e.inter / u if u else 0.0

@dataclass(frozen=True)
class Dice:
    def __call__(self, e):
        d = e.size_a + e.size_b
        return 2 * e.inter / d if d else 0.0

@dataclass(frozen=True)
class Coverage:                      # |a∩b|/|a| (or /|b|) — current min_overlap_ratio
    side: Literal["a", "b"] = "a"
    def __call__(self, e):
        s = e.size_a if self.side == "a" else e.size_b
        return e.inter / s if s else 0.0

@dataclass(frozen=True)
class OverlapCoefficient:            # Szymkiewicz–Simpson: inter / min(|a|,|b|)
    def __call__(self, e):           # absorbed "min" mode — symmetric, size-robust
        m = min(e.size_a, e.size_b)
        return e.inter / m if m else 0.0

@dataclass(frozen=True)
class CentroidProximity:             # 1 at coincident centers → 0 at d_max
    d_max_mm: float
    def __call__(self, e): return max(0.0, 1.0 - e.centroid_distance / self.d_max_mm)

@dataclass(frozen=True)
class CentroidContainment:           # 1 if a's centroid is inside b
    def __call__(self, e): return 1.0 if e.a_centroid_in_b else 0.0

@dataclass(frozen=True)
class Weighted:                      # composite: α·IoU + β·proximity, etc.
    terms: tuple[tuple[float, Measure], ...]
    def __call__(self, e): return sum(w * m(e) for w, m in self.terms)
```

`OverlapCoefficient` is the absorbed overlap-coefficient. `OverlapCoefficient()` with
`min_score=0.05` reproduces `nir_liver`'s intended 5% rule — and because tolerance is a single
`min_score` on a single measure, the F1 inert-threshold bug **cannot recur**.

### Starter matchers (`frozen` dataclasses carrying `measure` + `min_score`)

| Strategy | Behavior | Mirrors | v1? |
|---|---|---|---|
| `ThresholdMatch(measure, min_score)` | keep every edge scoring ≥ threshold; groups = connected components of surviving edges (1:N, N:M preserved) | current `intersection`/`difference` | ✅ |
| `GreedyArgmax(measure, min_score, direction)` | sort edges by score ↓, assign greedily, each node used once; **winner-take-all** | the 1:N "take/delete the larger" | ✅ |
| `Hungarian(measure, min_score)` | `scipy.optimize.linear_sum_assignment`, optimal 1:1 | MOTA / Panoptic | follow-up |
| `ConnectedClusters(measure, min_score)` | each connected component of the graph = one `MatchGroup`; no winner chosen | Panoptic Quality | follow-up |

`GreedyArgmax.direction`: `"mutual"` (mutual-best — for `symmetric_difference`) vs `"b_to_a"` / `"a_to_b"`
(each component on one side takes its best partner on the other; the other side may receive several —
for `append` and for "delete the larger"). One matcher covers both directed and symmetric cases.

`Hungarian`/`ConnectedClusters` are ~5–10 lines each and serve as the first **examples of extension**
(add a strategy = implement the `Protocol`); not required for v1.

### Entry point + the single impure builder

```python
def correspond(a, b, *, spacing, strategy: MatchingStrategy) -> Correspondence:
    return strategy(build_overlap_graph(a, b, spacing=spacing))
```

`build_overlap_graph` is the only graph-side array code: pairwise `|a∩b|` via the vectorized
contingency-table trick (`a.astype(int64) * (b.max() + 1) + b`, then `np.unique(..., return_counts=True)`,
O(voxels)); sizes from the same unique-counts; centroids via **per-label coordinate means (pure
numpy)**; `centroid_in_mask` by indexing the other labelmap at the rounded centroid. No skimage — the
`Segmentation` adapter may pass its cached `regionprops` (sizes/centroids) to skip recomputation, but
the core needs none. `Hungarian` lazily imports `scipy` inside `__call__` (like `reindex_to`), so the
module import stays light. Everything downstream of the builder is pure.

## Layer 3 — operations + `Segmentation` integration

An operation is a **pure function `Correspondence -> KeepPlan`**; painting is one mechanical `render`.

```python
@dataclass(frozen=True)
class KeepPlan:
    from_a: tuple[tuple[int, int], ...]   # (source label in A, out value; 0 = relabel)
    from_b: tuple[tuple[int, int], ...]

class SetOperation(Protocol):
    def __call__(self, corr: Correspondence) -> KeepPlan: ...

def render(plan, a, b, *, base=None, relabel=True) -> np.ndarray:
    ...   # mechanical: base=None → blank canvas; base=self.img → overlay (append)
```

Parameterized operations are `frozen` dataclasses implementing `SetOperation`; parameterless ones may
be classes too, for uniformity.

| Operation | Policy over `Correspondence` |
|---|---|
| `SymmetricDifference()` | `unmatched_a` + `unmatched_b` (disagreeing on both sides) |
| `Difference()` (A−B) | `unmatched_a` |
| `Intersection()` | A-labels from `matches` |
| `AppendMerge()` | for each group, paint its `b_labels` with value `a_labels[0]` over a copy of self |
| `DeleteMatched(side)` | drop matched, keep `unmatched_<side>` |

**Tolerance moved into the matcher.** `Difference` with `max_overlap=k` is just
`ThresholdMatch(AbsoluteOverlap(), min_score=k+1)` (A overlapping ≤ k stays unmatched → kept). The
operation is always "take `unmatched_a`", with no thresholds inside it.

### The headline case (the original request), end-to-end

"One A-component overlaps two B-components; delete only the B with the larger overlap":

```python
corr = correspond(a.img, b.img, spacing=a.spacing,
                  strategy=GreedyArgmax(AbsoluteOverlap(), direction="a_to_b"))
out  = render(DeleteMatched(side="b")(corr), a.img, b.img, base=b.img)
```

`GreedyArgmax` resolves the 1:N to a winner (larger B); `DeleteMatched(side="b")` removes the winner,
the smaller B survives. To switch the tie-break to "closer centroid" change **only the measure**:
`GreedyArgmax(CentroidProximity(d_max_mm=...), ...)` — operations untouched.

### `Segmentation` adapters + backward compatibility

Methods keep their signatures and gain an optional `strategy=`:

```python
def symmetric_difference(self, other, *, strategy=None,
                         min_overlap=1, min_overlap_ratio=None, ..., resample=False):
    other = self._align_other(other, resample=resample)
    strategy = strategy or ThresholdMatch(_measure_from(min_overlap, min_overlap_ratio), min_score=...)
    corr = correspond(self.img, other.img, spacing=self.spacing, strategy=strategy)
    out = Segmentation(template=self)
    out.img = render(SymmetricDifference()(corr), self.img, other.img)
    return out
```

- Old scalar params, when no `strategy=` is given, build the **default `ThresholdMatch`** → existing
  callers and tests unchanged.
- An explicit `strategy=GreedyArgmax(...)` takes precedence — the new capability.
- Deprecated operators (`&`, `|`, `-`, `^`, `+`) keep delegating.

**Exact backward-compat mapping** (working paths):

| Current call | New equivalent |
|---|---|
| `intersection(min_overlap=N)` | `ThresholdMatch(AbsoluteOverlap(), min_score=N)` + `Intersection()` |
| `intersection(min_overlap_ratio=r)` | `ThresholdMatch(Coverage("a"), min_score=r)` + `Intersection()` |
| `difference(max_overlap=k)` | `ThresholdMatch(AbsoluteOverlap(), min_score=k+1)` + `Difference()` |

Integer-overlap mappings are exact; ratio boundaries use `≥`.

**Two intentional changes (not bit-for-bit):**

1. **`symmetric_difference`** default semantics become **clean component-level** — no `union` relabel,
   so a connected agreeing+disagreeing blob no longer drops wholesale (the very "limitation" that
   motivated this). Existing `symmetric_difference` tests are updated to the improved result, change
   documented in `docs/image-service.md`.
2. **`difference(max_overlap_ratio=...)`** is **fixed**, not preserved: the inert F1 path is replaced
   by `ThresholdMatch(Coverage("a"), min_score=r)` (or `OverlapCoefficient` — see consumers). The old
   behavior was dead code, so nothing real depends on the broken semantics.

## Slicer `subtract_segmentations` reuse

`clarinet/services/slicer/helper.py::subtract_segmentations` (helper.py:1877) runs **inside Slicer**
and cannot import the heavy image stack. Today it removes segments with
`remove = overlap > max_overlap AND (overlap/total > max_overlap_ratio)` (helper.py:1937–1939) — its
own AND-of-two-gates.

Because the pure core (`PairStats`, measures, matchers, `build_overlap_graph`) depends only on
`numpy`, the Slicer side reuses the same math: either **import**
`correspondence.py` directly if the Slicer Python env has numpy (it does — `arr_b` is already a numpy
labelmap), or **vendor** the handful of pure functions. Either way the per-label decision is the same
single-source-of-truth `OverlapCoefficient` + `min_score` logic, not a re-derived AND. The extracted
math is unit-testable Slicer-free (helper.py already imports under a `_Dummy` fallback).

## Consumers & rollout (inherited from the superseded spec — verify at implementation)

### nir_liver — core fix
- `plan/workflows/tasks.py:523,527` (`compare_w_projection`) — the two `difference` calls
  (`max_overlap_ratio=0.05` already present) move to the overlap-coefficient measure
  (`strategy=ThresholdMatch(OverlapCoefficient(), min_score=0.05)` via the new API, or the
  back-compat shim). This is what actually activates the intended 5% rule.

### nir_liver — Slicer scripts
- `plan/scripts/second_review.py:25` — `subtract_segmentations(projection, doctor_seg, "MissedLesions", ...)`
  switches to the overlap-coefficient decision at `0.05`. Result ("missed") feeds the reviewer pool.
- `plan/scripts/update_master_model.py:77` — `subtract_segmentations(doctor_islands, projection, ...)`
  likewise. Result grows the persistent master model. **Accepted consequence:** borderline doctor
  lesions overlapping an existing master lesion by `coef < 5%` are now treated as FP and added, where
  the old "1 voxel = matched" rule suppressed them.

### Rollout
- **No DB migration** — FN/FP list + counts shape unchanged.
- **Two repos, ordered**: the new API must land in clarinet before `nir_liver` calls it. Confirm how
  clarinet is wired into `nir_liver` (shared venv / PYTHONPATH / wheel) — decides whether a clarinet
  release is needed between PRs or an editable install suffices. ≥2 PRs (clarinet → nir_liver).
- **Recompute existing patients** (operator action, out of code scope): existing
  `compare-with-projection` records hold FN/FP under the old "1 voxel" rule. Re-trigger compare per
  series (invalidate → `pending`). Expect **more FNs** (5%-coef is stricter); the "invalidate
  second-review on new FN" logic will fire a wave of re-openings — flag to the operator.

## Testing

Pure layers test with no images. Two array-touching units (`build_overlap_graph`, `render`) get
small synthetic-array tests.

- `tests/test_correspondence.py` (new, fast, no I/O): measures, matchers, operations, render,
  build_overlap_graph.
- `tests/test_image.py` (extend): `Segmentation` adapters + backward compat.

| Group | Build | Assert |
|---|---|---|
| Measures | `PairStats(...)` literal | `IoU(PairStats(inter=4, size_a=8, size_b=8))==1/3`; `OverlapCoefficient` small-inside-large; div-by-zero guards |
| Matchers | `OverlapGraph` literal | full taxonomy 1:0 / 0:1 / 1:1 / **1:N** / N:1 / N:M; threshold; deterministic tie-break |
| Operations | `Correspondence` literal | `KeepPlan`: which labels / side; matched dropped vs kept |
| render | mini labelmap (4×4×1) + `KeepPlan` | painting, `relabel`, `base=self.img` overlay |
| build_graph | mini labelmaps | `inter`/sizes; `centroid_in_mask` incl. C-shape (centroid outside → `False`); `spacing` scales distance |
| Segmentation | small `Segmentation` | back-compat defaults; new `strategy=`; improved symdiff |

Two headline tests (the whole point):

```python
def test_greedy_argmax_resolves_1_to_n_by_overlap():
    g = graph(a=[comp(1, size=20)],
              b=[comp(1, size=12), comp(2, size=6)],
              edges=[pair(a=1, b=1, inter=10), pair(a=1, b=2, inter=3)])
    corr = GreedyArgmax(AbsoluteOverlap(), direction="a_to_b")(g)
    assert corr.matches == (MatchGroup(a_labels=(1,), b_labels=(1,), score=10.0),)
    assert corr.unmatched_b == (2,)          # smaller-overlap loser kept

def test_measure_swap_changes_winner():       # b2: smaller volume, closer centroid
    g = graph(a=[comp(1, size=20)],
              b=[comp(1, size=12), comp(2, size=6)],
              edges=[pair(a=1, b=1, inter=10, dist=8.0),
                     pair(a=1, b=2, inter=3,  dist=1.0)])
    corr = GreedyArgmax(CentroidProximity(d_max_mm=10), direction="a_to_b")(g)
    assert corr.matches[0].b_labels == (2,)  # same machine, different measure → different winner
```

Mini factories (`comp`, `pair`, `graph`, `corr`) keep tests one-liners. Regression guards: existing
`intersection`/`difference` (working paths) stay green; `append` still raises on multi-label by
default, `append(strategy=GreedyArgmax(direction="b_to_a"))` resolves (opt-in); one test where the old
`union`-relabel dropped a connected blob and the new symdiff keeps the disagreeing part.

Optional property tests (in the spirit of `tests/schema/`): `symdiff(a,b) ≡ symdiff(b,a)` up to
relabeling; every component lands in exactly one of matched / unmatched_a / unmatched_b. YAGNI.

Run: `./scripts/run_tests.sh -k correspondence -q` (fast), then `make test-fast`.

## Migration / sequencing

1. **clarinet PR 1** — `correspondence.py` (data model, measures, matchers, operations, render,
   `correspond`/`build_overlap_graph`) + `Segmentation` adapters + back-compat + `test_correspondence.py`
   + updated `symmetric_difference` tests + `docs/image-service.md`. Minor version bump (new public API).
2. **clarinet PR 2** — `subtract_segmentations` switches to the shared overlap-coefficient core;
   integration test.
3. **nir_liver PR(s)** — `compare_w_projection` and the two Slicer scripts move to the
   overlap-coefficient decision at `0.05`; focused asymmetry test (small-inside-large).
4. **Operator** — recompute existing patients; expect more FNs + a re-opening wave.

## Open items

- **`symmetric_difference` legacy thresholds.** The old method took *two* thresholds (`min_overlap`
  for its intersection half, `max_overlap` for its difference half); the new model has a single match
  threshold. Define the exact collapse: `min_overlap` drives the single match cut-off,
  `max_overlap`/`*_ratio` deprecated for symdiff (kept as accepted-but-ignored params with a warning,
  or dropped). Pick one and document.
- Confirm `Segmentation.img` setter interaction in adapters (autolabel must not re-run when painting
  `render` output; assign the final array once).
- `render` output-labeling policy default (`relabel=True` sequential vs preserve source value) — pick
  per operation; `AppendMerge` must preserve the target A-label.
- Whether `subtract_segmentations` imports `correspondence.py` directly (verify Slicer env has the
  pure core's deps) or vendors it.
- nir_liver clarinet wiring (venv / PYTHONPATH / wheel) — decides release ordering between PR 1 and the
  nir_liver PRs.
