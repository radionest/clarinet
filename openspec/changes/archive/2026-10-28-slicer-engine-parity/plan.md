# Slicer Engine Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `SlicerHelper.subtract_segmentations` and `Segmentation.difference` decide removals through the identical engine path (`correspond()` → `Difference()` → `KeepPlan`) with the identical parameter set.

**Architecture:** The scalars→strategy derivation moves from `segmentation.py` into `correspondence/matching.py` so the Slicer bundle carries it (D7). The helper's hand-rolled voxel loop is replaced by the bundled engine (D6), with granularity an explicit parameter (D8). Production script paths always supply the bundle (D9); a signature-parity test stops future drift (D10).

**Tech Stack:** Python 3.12 (server) / Slicer's Python 3.9 (helper + bundle), numpy, FastAPI, pytest. No new dependencies.

**Artifacts:** `openspec/changes/slicer-engine-parity/` — `design.md` (D6–D10), `specs/slicer-comparison-engine/spec.md`, `specs/slicer-overlap-detection/spec.md`, `tasks.md`. Plan tasks ↔ tasks.md mapping noted per task.

**Execution context:** enter a worktree first (`EnterWorktree`), then link the openspec store: `MAIN=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)"); [ -e openspec ] || ln -s "$MAIN/openspec" openspec`. First `uv`/`pytest` run in a fresh worktree builds the venv — allow `timeout 300`.

## Global Constraints

- `correspondence/*.py` source must stay free of the token `clarinet` outside stripped import lines — **including comments and docstrings** (`test_bundle_has_no_clarinet_imports` scans every line) — and must parse under Python 3.9 grammar (`ast.parse(..., feature_version=(3, 9))` gate).
- `helper.py` runs inside Slicer's Python 3.9: no 3.10+ syntax; no `clarinet` imports at runtime (TYPE_CHECKING-only allowed); single-file contract.
- Bundle symbols reach `helper.py` **only** via `execute(..., include_correspondence=True)`; helper methods read them from module `globals()` (pattern: `detect_overlaps`, `helper.py:2144`).
- `.claude/rules/slicer-helper-api.md` and `clarinet/docs/slicer-helper-api.md` must stay byte-identical (golden guard in `tests/test_agent_scaffold.py`).
- All commands through `uv run`; final gate `make check`. Test output → `> /tmp/test-slicer-engine-parity.txt 2>&1`, never piped.
- Conventional commits, English, no Co-Authored-By tags.

## Validated test selectors (pinned 2026-07-21, pre-change)

| Selector | Collected now | Expected after |
|---|---|---|
| `tests/test_slicer_correspondence_bundle.py` | 4 | 6 (Task 1) |
| `tests/test_image.py -k "difference"` | 7 | 9 (Task 2) |
| `tests/test_slicer_engine_parity.py` (new) | — | 6 (Tasks 3–4) |
| `tests/test_slicer_build_script.py` | 9 | 9 (unchanged) |
| `tests/integration/test_record_submit_execresult.py` | 9 | 10 (Task 5) |
| `tests/integration/test_slicer_router_bundle.py` (new) | — | 4 (Task 5) |
| `tests/integration/test_slicer_helper.py` (live-Slicer-gated) | 37 | 39 (Task 6) |
| `tests/test_agent_scaffold.py` | 17 | 17 (Task 7 gate) |

---

### Task 1: Shared scalars→strategy derivation (D7; tasks.md 1.1–1.2)

**Files:**
- Modify: `clarinet/services/image/correspondence/matching.py` (append function)
- Modify: `clarinet/services/image/correspondence/__init__.py` (export)
- Modify: `clarinet/services/image/segmentation.py:506-510` (call the shared helper)
- Test: `tests/test_slicer_correspondence_bundle.py`

**Interfaces:**
- Produces: `strategy_from_thresholds(max_overlap: int = 0, max_overlap_ratio: float | None = None) -> ThresholdMatch` — importable from `clarinet.services.image.correspondence`; present in the exec'd bundle namespace (used by Tasks 2, 3).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_slicer_correspondence_bundle.py`:

```python
def test_bundle_exposes_strategy_derivation() -> None:
    """The scalars->strategy derivation must ship inside the bundle (D7)."""
    ns: dict = {"__name__": "_bundle"}
    exec(build_correspondence_bundle(), ns)
    assert callable(ns["strategy_from_thresholds"])


def test_bundle_derivation_matches_native() -> None:
    """Identical scalars -> identical strategies, bundled and native alike."""
    from clarinet.services.image.correspondence.matching import strategy_from_thresholds

    ns: dict = {"__name__": "_bundle"}
    exec(build_correspondence_bundle(), ns)
    cases = [
        {},
        {"max_overlap": 3},
        {"max_overlap_ratio": 0.25},
        {"max_overlap": 3, "max_overlap_ratio": 0.25},  # ratio takes precedence
    ]
    for kwargs in cases:
        bundled = ns["strategy_from_thresholds"](**kwargs)
        native = strategy_from_thresholds(**kwargs)
        assert type(bundled.measure).__name__ == type(native.measure).__name__
        assert bundled.min_score == native.min_score
        assert getattr(bundled.measure, "side", None) == getattr(native.measure, "side", None)
```

- [ ] **Step 2: Run to verify both fail**

Run: `uv run pytest tests/test_slicer_correspondence_bundle.py -q`
Expected: 2 failed (`KeyError: 'strategy_from_thresholds'` / ImportError), 4 passed.

- [ ] **Step 3: Implement** — append to `clarinet/services/image/correspondence/matching.py` (after `GreedyArgmax`). The file currently imports only from `.model`; add a measures import in the import block (ruff orders `measures` before `model`):

```python
from clarinet.services.image.correspondence.measures import AbsoluteOverlap, Coverage
```

```python
def strategy_from_thresholds(
    max_overlap: int = 0, max_overlap_ratio: float | None = None
) -> ThresholdMatch:
    """Derive the difference-matching strategy from legacy scalar thresholds.

    Ratio takes precedence: with ``max_overlap_ratio`` set, a label is matched
    (removed from a difference) iff ``inter / size_a >= max_overlap_ratio``
    (``Coverage("a")``), and ``max_overlap`` is ignored. Otherwise
    ``AbsoluteOverlap`` with ``min_score = max_overlap + 1`` keeps labels whose
    largest single-pair overlap is at most ``max_overlap``.
    """
    if max_overlap_ratio is not None:
        return ThresholdMatch(Coverage("a"), min_score=max_overlap_ratio)
    return ThresholdMatch(AbsoluteOverlap(), min_score=float(max_overlap + 1))
```

Bundle-safety notes (already satisfied, no bundler change): `_MODULES` order in `clarinet/services/slicer/correspondence_bundle.py:14` is `(model, measures, matching, ...)` — measures' names are defined before `matching`'s source executes; the new import line is stripped like every `from clarinet ...` line. The docstring must not contain the token `clarinet` (Global Constraints).

In `clarinet/services/image/correspondence/__init__.py`: change the matching import line to

```python
from clarinet.services.image.correspondence.matching import (
    GreedyArgmax,
    ThresholdMatch,
    strategy_from_thresholds,
)
```

and add `"strategy_from_thresholds"` to `__all__` (keep alphabetical position: after `"render"`).

In `clarinet/services/image/segmentation.py:506-510` replace:

```python
        if strategy is None:
            if max_overlap_ratio is not None:
                strategy = ThresholdMatch(Coverage("a"), min_score=max_overlap_ratio)
            else:
                strategy = ThresholdMatch(AbsoluteOverlap(), min_score=float(max_overlap + 1))
```

with:

```python
        if strategy is None:
            strategy = strategy_from_thresholds(max_overlap, max_overlap_ratio)
```

Add `strategy_from_thresholds` to the first correspondence import block (`segmentation.py:22-29`). Do **not** remove `ThresholdMatch`/`Coverage`/`AbsoluteOverlap` imports — `symmetric_difference` still uses them (`segmentation.py:580-581`, a min-threshold derivation that stays put).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_slicer_correspondence_bundle.py tests/test_image.py -q > /tmp/test-slicer-engine-parity.txt 2>&1`
Expected: bundle file 6 passed; `test_image.py` all passed (the 7 `-k "difference"` regression tests among them).

- [ ] **Step 5: Commit**

```bash
git add clarinet/services/image/correspondence/matching.py clarinet/services/image/correspondence/__init__.py clarinet/services/image/segmentation.py tests/test_slicer_correspondence_bundle.py
git commit -m "feat(correspondence): share the scalars-to-strategy derivation"
```

---

### Task 2: Granularity selector on `Segmentation.difference` (D6, D8; tasks.md 2.1–2.2)

**Files:**
- Modify: `clarinet/services/image/segmentation.py:465-515` (`difference`)
- Test: `tests/test_image.py`

**Interfaces:**
- Consumes: `strategy_from_thresholds` (Task 1).
- Produces: `Segmentation.difference(other, *, strategy=None, max_overlap=0, max_overlap_ratio=None, granularity: Literal["label", "union"] = "label", resample=False)`. Value naming is final: `"label"` = each label of `other` scored separately (current behavior); `"union"` = `other` flattened to one mask before the graph. Task 4's parity test and Task 3's helper use these exact names.

- [ ] **Step 1: Write the failing tests** — add to `tests/test_image.py`, module level next to `test_difference_intersection_multi_component_per_edge_threshold` (line 1875); both names contain `difference` so the pinned `-k "difference"` selector grows 7 → 9:

```python
def _fragmented_pair() -> tuple[Segmentation, Segmentation]:
    """Base label (10 vox) overlapped by two disconnected b-components, 3 vox each.

    Per-pair coverage 0.3 < 0.5 threshold; joint coverage 0.6 >= 0.5.
    """
    vol_a = np.zeros((10, 10, 5), dtype=np.uint8)
    vol_a[1, 0:10, 1] = 1  # 10 voxels in a row
    vol_b = np.zeros((10, 10, 5), dtype=np.uint8)
    vol_b[1, 0:3, 1] = 1  # overlaps 3 voxels
    vol_b[1, 5:8, 1] = 1  # disjoint, overlaps 3 more
    seg_a = Segmentation()
    seg_a._spacing = (1.0, 1.0, 1.0)
    seg_a.img = vol_a
    seg_b = Segmentation()
    seg_b._spacing = (1.0, 1.0, 1.0)
    seg_b.img = vol_b  # autolabel -> two components, labels 1 and 2
    return seg_a, seg_b


def test_difference_default_granularity_keeps_fragmented_overlap() -> None:
    seg_a, seg_b = _fragmented_pair()
    result = seg_a.difference(seg_b, max_overlap_ratio=0.5)
    assert not result.is_empty  # each pair 0.3 < 0.5 -> kept


def test_difference_union_granularity_removes_fragmented_overlap() -> None:
    seg_a, seg_b = _fragmented_pair()
    result = seg_a.difference(seg_b, max_overlap_ratio=0.5, granularity="union")
    assert result.is_empty  # joint 0.6 >= 0.5 -> removed
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_image.py -k "difference" -q`
Expected: `test_difference_union_granularity_removes_fragmented_overlap` fails with `TypeError: ... unexpected keyword argument 'granularity'`; 8 others pass.

- [ ] **Step 3: Implement** — in `difference` (`segmentation.py:465`):

Signature (insert `granularity` between `max_overlap_ratio` and `resample`):

```python
    def difference(
        self,
        other: Self,
        *,
        strategy: MatchingStrategy | None = None,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
        granularity: Literal["label", "union"] = "label",
        resample: bool = False,
    ) -> Segmentation:
```

Body — after the `self._align_other(...)` line, flatten before building the graph:

```python
        other = self._align_other(other, resample=resample)  # type: ignore[assignment]
        other_img = (other.img > 0).astype(np.uint8) if granularity == "union" else other.img
        if strategy is None:
            strategy = strategy_from_thresholds(max_overlap, max_overlap_ratio)
        corr = correspond(self.img, other_img, spacing=self.spacing, strategy=strategy)
```

(`render(...)` at line 514 keeps `other.img` — `Difference`'s `KeepPlan.from_b` is always empty, the b-array is never painted.)

Docstring: add under Args —

```
            granularity: How ``other`` enters the overlap graph. ``"label"``
                (default): each label scored separately — a label of ``self`` is
                removed when its best single-pair score meets the threshold.
                ``"union"``: all labels of ``other`` flattened to one mask —
                ``self`` labels are scored against their combined extent
                (legacy Slicer sum-over-union semantics).
```

and note in the strategy description that the default derivation is the shared `strategy_from_thresholds`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_image.py -q > /tmp/test-slicer-engine-parity.txt 2>&1`
Expected: all passed; `-k "difference"` now collects 9.

- [ ] **Step 5: Commit**

```bash
git add clarinet/services/image/segmentation.py tests/test_image.py
git commit -m "feat(image): explicit granularity selector on Segmentation.difference"
```

---

### Task 3: Engine-backed `subtract_segmentations` (D6, D8; tasks.md 3.1–3.3 opt-in edits)

**Files:**
- Modify: `clarinet/services/slicer/helper.py:2009-2098` (`subtract_segmentations`), `helper.py:1639-1646` (TYPE_CHECKING block)
- Modify: `tests/integration/test_slicer_helper.py:647,757` (bundle opt-in on the two live tests that execute subtract)
- Create: `tests/test_slicer_engine_parity.py`

**Interfaces:**
- Consumes: `strategy_from_thresholds` (Task 1), granularity value names `"label"`/`"union"` (Task 2), bundle namespace symbols `correspond`, `Difference` (existing).
- Produces: `SlicerHelper.subtract_segmentations(seg_a, seg_b, output_name=None, max_overlap=0, max_overlap_ratio=None, resample=False, *, strategy: Any = None, granularity: Literal["label", "union"] = "label")` — raises `SlicerHelperError` when the bundle is absent. Task 4 reads this signature; Task 5 makes the bundle reach it in production.

- [ ] **Step 1: Write the failing no-Slicer tests** — create `tests/test_slicer_engine_parity.py`:

```python
"""Cross-runtime engine-parity contract (CI-only, no Slicer required).

The engine-backed ``subtract_segmentations`` must (a) refuse to run without
the correspondence bundle, and (b) reach the same removal verdicts as
``Segmentation.difference`` when the bundled engine is fed the same arrays.
"""

import numpy as np
import pytest

from clarinet.services.image.segmentation import Segmentation
from clarinet.services.slicer.correspondence_bundle import build_correspondence_bundle
from clarinet.services.slicer.helper import SlicerHelper, SlicerHelperError


def _bundle_ns() -> dict:
    ns: dict = {"__name__": "_bundle"}
    exec(build_correspondence_bundle(), ns)
    return ns


def _bundle_keep_labels(ns: dict, arr_a: np.ndarray, arr_b: np.ndarray, **kwargs) -> set[int]:
    """The exact decision path subtract_segmentations runs inside Slicer."""
    strategy = kwargs.pop("strategy", None) or ns["strategy_from_thresholds"](**kwargs)
    corr = ns["correspond"](arr_a, arr_b, spacing=(1.0, 1.0, 1.0), strategy=strategy)
    plan = ns["Difference"]()(corr)
    return {lbl for lbl, _out in plan.from_a}


def _seg(arr: np.ndarray, autolabel: bool = True) -> Segmentation:
    seg = Segmentation(autolabel=autolabel)
    seg._spacing = (1.0, 1.0, 1.0)
    seg.img = arr
    return seg


def _server_keep_labels(arr_a: np.ndarray, arr_b: np.ndarray, **kwargs) -> set[int]:
    out = _seg(arr_a, autolabel=False).difference(_seg(arr_b, autolabel=False), **kwargs)
    return {int(v) for v in np.unique(out.img) if v}


def test_subtract_raises_without_bundle() -> None:
    """No hand-rolled fallback: raise before touching operands (spec scenario)."""
    helper = SlicerHelper.__new__(SlicerHelper)  # __init__ needs a live scene
    with pytest.raises(SlicerHelperError, match="correspondence bundle"):
        helper.subtract_segmentations(object(), object())


def _two_label_case() -> tuple[np.ndarray, np.ndarray]:
    """Label 1 heavily covered (4/8), label 2 barely (1/8) — component-labeled arrays."""
    arr_a = np.zeros((10, 10, 5), dtype=np.uint8)
    arr_a[1:3, 1:3, 1:3] = 1  # 8 voxels
    arr_a[6:8, 6:8, 1:3] = 2  # 8 voxels
    arr_b = np.zeros((10, 10, 5), dtype=np.uint8)
    arr_b[1:3, 1:3, 1:2] = 1  # covers 4 of label 1
    arr_b[6:7, 6:7, 1:2] = 2  # covers 1 of label 2
    return arr_a, arr_b


def test_bundle_decision_matches_difference_ratio() -> None:
    arr_a, arr_b = _two_label_case()
    kwargs = {"max_overlap_ratio": 0.5}
    keep = _bundle_keep_labels(_bundle_ns(), arr_a, arr_b, **kwargs)
    assert keep == _server_keep_labels(arr_a, arr_b, **kwargs) == {2}


def test_bundle_decision_exact_ratio_threshold_removes() -> None:
    """Boundary is >= on both call sites (legacy Slicer used strict >)."""
    arr_a, arr_b = _two_label_case()
    kwargs = {"max_overlap_ratio": 0.125}  # label 2 coverage exactly 1/8
    keep = _bundle_keep_labels(_bundle_ns(), arr_a, arr_b, **kwargs)
    assert keep == _server_keep_labels(arr_a, arr_b, **kwargs) == set()


def test_bundle_decision_matches_difference_absolute() -> None:
    arr_a, arr_b = _two_label_case()
    kwargs = {"max_overlap": 2}  # tolerate <=2: label 1 (4) removed, label 2 (1) kept
    keep = _bundle_keep_labels(_bundle_ns(), arr_a, arr_b, **kwargs)
    assert keep == _server_keep_labels(arr_a, arr_b, **kwargs) == {2}


def test_explicit_strategy_ignores_scalars() -> None:
    """Spec: a supplied strategy wins over scalar thresholds on both call sites."""
    from clarinet.services.image.correspondence import IoU, ThresholdMatch

    arr_a, arr_b = _two_label_case()
    ns = _bundle_ns()
    # Scalars alone would remove label 1 (coverage 0.5 >= 0.5); IoU >= 0.99 keeps both.
    keep = _bundle_keep_labels(
        ns, arr_a, arr_b,
        max_overlap_ratio=0.5,
        strategy=ns["ThresholdMatch"](ns["IoU"](), min_score=0.99),
    )
    server = _server_keep_labels(
        arr_a, arr_b,
        max_overlap_ratio=0.5,
        strategy=ThresholdMatch(IoU(), min_score=0.99),
    )
    assert keep == server == {1, 2}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_slicer_engine_parity.py -q`
Expected: `test_subtract_raises_without_bundle` FAILS (no raise — legacy loop runs on `object()` operands and crashes differently, or `_unwrap_node` errors first); the four decision tests PASS already (they test engine internals shipped in Task 1). If a decision test fails here, stop — the derivation or engine moved wrongly in Task 1.

- [ ] **Step 3: Implement the helper rewrite** — `clarinet/services/slicer/helper.py`.

3a. Extend the TYPE_CHECKING block (`helper.py:1645-1646`) so mypy resolves the bundle names (comment above it already explains the mechanism — extend the import only):

```python
if TYPE_CHECKING:
    from clarinet.services.image.correspondence.graph import build_overlap_graph, correspond
    from clarinet.services.image.correspondence.matching import strategy_from_thresholds
    from clarinet.services.image.correspondence.operations import Difference
```

3b. Replace `subtract_segmentations` (`helper.py:2009-2098`) — signature, docstring, and decision core; the export/apply scaffolding stays as-is:

```python
    def subtract_segmentations(
        self,
        seg_a: SegmentationBuilder | Any,
        seg_b: SegmentationBuilder | Any,
        output_name: str | None = None,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
        resample: bool = False,
        *,
        strategy: Any = None,
        granularity: Literal["label", "union"] = "label",
    ) -> Any:
        """ROI-level subtraction backed by the shared correspondence engine.

        Removal decisions run the same path as the image service's
        ``Segmentation.difference``: ``correspond()`` -> ``Difference()`` ->
        ``KeepPlan``. Scalar thresholds derive the strategy via the bundled
        ``strategy_from_thresholds``: with ``max_overlap_ratio`` set the ratio
        wins (a segment is removed iff ``overlap / size >= max_overlap_ratio``
        against its best-scoring counterpart; ``max_overlap`` is ignored),
        otherwise a segment is removed when its largest single-pair overlap
        exceeds ``max_overlap`` voxels.

        Args:
            seg_a: Segmentation to subtract from (SegmentationBuilder or node).
            seg_b: Segmentation to subtract (SegmentationBuilder or node).
            output_name: If set, create a new node with surviving segments instead
                         of modifying seg_a in-place. Slicer-only node handling —
                         not part of the shared engine parameter set.
            max_overlap: Maximum allowed per-pair overlap voxel count.
                Ignored when ``strategy`` or ``max_overlap_ratio`` is set.
            max_overlap_ratio: Maximum allowed overlap ratio (overlap/segment size).
                Takes precedence over ``max_overlap``; boundary is ``>=``.
            resample: If True, skip the source-vs-volume geometry check and re-grid a
                mismatched input onto the reference extent (legacy behavior). Default
                False raises SlicerHelperError on a grid mismatch. Re-gridding is
                runtime-native (labelmap re-export here, ``reindex_to`` server-side) —
                near-threshold verdicts may differ between runtimes on this path.
            strategy: Matching-strategy override built from bundle symbols, e.g.
                ``ThresholdMatch(IoU(), min_score=0.5)``. When set, the scalar
                thresholds are ignored.
            granularity: How seg_b enters the overlap graph. ``"label"`` (default):
                each subtracted segment scored separately, matching
                ``Segmentation.difference``. ``"union"``: all subtracted segments
                flattened to one mask — each base segment is scored against their
                combined extent (the legacy sum-over-union rule).

        Returns:
            The output segmentation node (new node if output_name, else seg_a node).

        Raises:
            SlicerHelperError: The script was sent without the correspondence
                bundle — call ``execute(..., include_correspondence=True)`` (raises
                regardless of operand content); unknown ``granularity``; or a grid
                mismatch was detected (see ``_export_segments_labelmap``).
        """
        import numpy as np

        if "correspond" not in globals():
            raise SlicerHelperError(
                "subtract_segmentations requires the correspondence bundle; "
                "call execute(..., include_correspondence=True)."
            )
        if granularity not in ("label", "union"):
            raise SlicerHelperError(
                f"granularity must be 'label' or 'union', got {granularity!r}"
            )

        node_a = self._unwrap_node(seg_a)
        node_b = self._unwrap_node(seg_b)

        # Export both with mode 0 (reference geometry extent) → same shape
        labelmap_b, arr_b = self._export_segments_labelmap(
            node_b, "_sub_b", what="the subtracted segmentation (seg_b)", resample=resample
        )
        try:
            labelmap_a, arr_a = self._export_segments_labelmap(
                node_a, "_sub_a", what="the base segmentation (seg_a)", resample=resample
            )
        except SlicerHelperError:
            slicer.mrmlScene.RemoveNode(labelmap_b)
            raise

        vtk_seg_a = node_a.GetSegmentation()
        segments_to_remove: list[str] = []

        try:
            # A None array means a source was genuinely empty (already warned, tolerated):
            # an empty base subtracts to itself, an empty subtrahend removes nothing —
            # either way no segment is dropped, so skip the engine entirely.
            if arr_a is not None and arr_b is not None:
                if granularity == "union":
                    arr_b = (arr_b > 0).astype(np.uint8)
                if strategy is None:
                    strategy = strategy_from_thresholds(max_overlap, max_overlap_ratio)
                # Labelmap array axes are (z,y,x); GetSpacing() is (x,y,z) — reverse to match.
                sx, sy, sz = labelmap_a.GetSpacing()
                corr = correspond(arr_a, arr_b, spacing=(sz, sy, sx), strategy=strategy)
                keep = {lbl for lbl, _out in Difference()(corr).from_a}
                # Segments absent from the export (0 voxels) never enter the graph:
                # keep them, as the legacy loop did.
                present = {int(v) for v in np.unique(arr_a) if v}
                for i in range(vtk_seg_a.GetNumberOfSegments()):
                    label = i + 1  # merged labelmap: segment 0 → label 1
                    if label in present and label not in keep:
                        segments_to_remove.append(vtk_seg_a.GetNthSegmentID(i))
        finally:
            slicer.mrmlScene.RemoveNode(labelmap_a)
            slicer.mrmlScene.RemoveNode(labelmap_b)
```

The trailing `output_name` / in-place blocks (`helper.py:2083-2098`) are unchanged.

3c. Opt the two live tests into the bundle — `tests/integration/test_slicer_helper.py`; both currently end with `result = await slicer_service.execute(slicer_url, script, context=context)`:

- `test_setop_tolerates_emptied_source` (line 647) and `test_subtract_segmentations_guards_grid_mismatch` (line 757): change that call to

```python
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )
```

(The bundle guard precedes the export guard, so the grid-mismatch test still exercises `does not match the volume grid`. Slicer's exec namespace is session-sticky — every subtract-using test opts in explicitly rather than relying on ordering.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_slicer_engine_parity.py tests/test_slicer_build_script.py -q > /tmp/test-slicer-engine-parity.txt 2>&1`
Expected: 5 + 9 passed. (Live-Slicer file only collects here; full live run happens in Task 6.)

- [ ] **Step 5: Commit**

```bash
git add clarinet/services/slicer/helper.py tests/test_slicer_engine_parity.py tests/integration/test_slicer_helper.py
git commit -m "feat(slicer): back subtract_segmentations with the correspondence engine"
```

---

### Task 4: Parameter-parity enforcement (D10; tasks.md 4.1)

**Files:**
- Modify: `tests/test_slicer_engine_parity.py`

**Interfaces:**
- Consumes: final signatures from Tasks 2 and 3.

- [ ] **Step 1: Write the failing test** — append to `tests/test_slicer_engine_parity.py`:

```python
def test_engine_parameter_parity() -> None:
    """D10: one engine implies one parameter set — names must not drift apart.

    Runtime-only names are the documented exclusions: node handling on the
    Slicer side (operands + output_name), the operand on the image side.
    """
    import inspect

    sub = set(inspect.signature(SlicerHelper.subtract_segmentations).parameters)
    diff = set(inspect.signature(Segmentation.difference).parameters)
    assert sub - {"self", "seg_a", "seg_b", "output_name"} == diff - {"self", "other"}
```

- [ ] **Step 2: Run — expected to pass immediately** (Tasks 2–3 already aligned the signatures); verify it **fails when broken** by temporarily renaming one parameter mentally — do not commit a mutation. If it fails now, a signature diverged: fix the source, not the test.

Run: `uv run pytest tests/test_slicer_engine_parity.py -q`
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_slicer_engine_parity.py
git commit -m "test(slicer): enforce engine parameter parity across runtimes"
```

---

### Task 5: Bundle on production script paths (D9; tasks.md 5.1–5.3)

**Files:**
- Modify: `clarinet/api/routers/slicer.py:28-33` (`SlicerExecRequest`), `:62` (`/exec`), `:201` (`/open`), `:282` (`/validate`)
- Modify: `clarinet/api/routers/record.py:529-534` (`_process_submission` validator call)
- Modify: `tests/utils/urls.py` (new constants), `tests/integration/test_record_submit_execresult.py`
- Create: `tests/integration/test_slicer_router_bundle.py`

**Interfaces:**
- Consumes: `SlicerService.execute(..., *, include_correspondence: bool = False)` (existing, `service.py:54-62`).
- Produces: `SlicerExecRequest.include_correspondence: bool = False`; record open/validate/submit-validator always execute with `include_correspondence=True`. URL constants `SLICER_EXEC`, `SLICER_RECORD_OPEN`, `SLICER_RECORD_VALIDATE`.

- [ ] **Step 1: Write the failing tests.**

1a. `tests/utils/urls.py` — extend the `# --- Slicer ---` block:

```python
SLICER_EXEC = "/api/slicer/exec"
SLICER_RECORD_OPEN = "/api/slicer/records/{record_id}/open"
SLICER_RECORD_VALIDATE = "/api/slicer/records/{record_id}/validate"
```

1b. Create `tests/integration/test_slicer_router_bundle.py` (fixture pattern mirrors `test_record_submit_execresult.py:44-100`):

```python
"""D9: production script paths supply the correspondence engine.

Slicer is mocked via dependency override — asserts the ``include_correspondence``
flag on ``SlicerService.execute``; flag→payload mapping is covered by
``tests/test_slicer_build_script.py::test_build_script_with_correspondence_opt_in``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from clarinet.api.app import app
from clarinet.api.dependencies import get_slicer_service
from clarinet.models.base import DicomQueryLevel
from clarinet.models.record import Record, RecordStatus, RecordType
from clarinet.models.study import Study
from clarinet.services.slicer.service import SlicerService
from tests.utils.factories import make_patient
from tests.utils.urls import SLICER_EXEC, SLICER_RECORD_OPEN, SLICER_RECORD_VALIDATE


@pytest_asyncio.fixture
async def scripted_record(test_session) -> Record:
    """Patient + study + RecordType with both Slicer scripts + pending Record."""
    patient = make_patient("BUNDLE_PAT001", "Bundle Path Patient")
    test_session.add(patient)
    await test_session.flush()

    study = Study(
        patient_id=patient.id,
        study_uid="2.16.840.1.999.902.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.flush()

    rt = RecordType(
        name="bundle-path-test",
        level=DicomQueryLevel.STUDY,
        slicer_script="pass  # stub — execution is mocked",
        slicer_result_validator="pass  # stub — execution is mocked",
    )
    test_session.add(rt)
    await test_session.flush()

    rec = Record(
        record_type_name=rt.name,
        patient_id=patient.id,
        study_uid=study.study_uid,
        status=RecordStatus.pending,
    )
    test_session.add(rec)
    await test_session.commit()
    await test_session.refresh(rec)
    return rec


@pytest_asyncio.fixture
async def mock_slicer_service() -> AsyncGenerator[AsyncMock]:
    mock = AsyncMock(spec=SlicerService)
    mock.execute.return_value = {}

    async def _override() -> SlicerService:
        return mock  # type: ignore[return-value]

    app.dependency_overrides[get_slicer_service] = _override
    try:
        yield mock
    finally:
        app.dependency_overrides.pop(get_slicer_service, None)


def _bundle_flag(mock: AsyncMock) -> bool:
    return bool(mock.execute.call_args.kwargs.get("include_correspondence", False))


@pytest.mark.asyncio
async def test_exec_excludes_bundle_by_default(client, mock_slicer_service: AsyncMock):
    resp = await client.post(SLICER_EXEC, json={"script": "pass"})
    assert resp.status_code == 200, resp.text
    assert _bundle_flag(mock_slicer_service) is False


@pytest.mark.asyncio
async def test_exec_forwards_bundle_opt_in(client, mock_slicer_service: AsyncMock):
    resp = await client.post(SLICER_EXEC, json={"script": "pass", "include_correspondence": True})
    assert resp.status_code == 200, resp.text
    assert _bundle_flag(mock_slicer_service) is True


@pytest.mark.asyncio
async def test_open_record_includes_bundle(
    client, scripted_record: Record, mock_slicer_service: AsyncMock
):
    resp = await client.post(SLICER_RECORD_OPEN.format(record_id=scripted_record.id))
    assert resp.status_code == 200, resp.text
    assert _bundle_flag(mock_slicer_service) is True


@pytest.mark.asyncio
async def test_validate_record_includes_bundle(
    client, scripted_record: Record, mock_slicer_service: AsyncMock
):
    resp = await client.post(SLICER_RECORD_VALIDATE.format(record_id=scripted_record.id))
    assert resp.status_code == 200, resp.text
    assert _bundle_flag(mock_slicer_service) is True
```

1c. Append to `tests/integration/test_record_submit_execresult.py`:

```python
@pytest.mark.asyncio
async def test_submit_validator_receives_bundle(
    client, cropping_box_record: Record, mock_slicer_service: AsyncMock
):
    """D9: the submit-path validator always gets the correspondence engine."""
    resp = await client.post(f"{RECORDS_BASE}/{cropping_box_record.id}/submit", json={})

    assert resp.status_code == 200, resp.text
    assert mock_slicer_service.execute.call_args.kwargs.get("include_correspondence") is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_slicer_router_bundle.py tests/integration/test_record_submit_execresult.py -q`
Expected: `test_exec_excludes_bundle_by_default` passes; the three `includes_bundle` tests + `test_submit_validator_receives_bundle` FAIL (flag absent); `test_exec_forwards_bundle_opt_in` fails (unknown field ignored by pydantic → flag never forwarded).

- [ ] **Step 3: Implement.**

`clarinet/api/routers/slicer.py`:

```python
class SlicerExecRequest(BaseModel):
    """Request model for script execution."""

    script: str
    context: dict[str, Any] | None = None
    include_correspondence: bool = False
```

`/exec` (line 62):

```python
    return await service.execute(
        slicer_url,
        request.script,
        request.context,
        include_correspondence=request.include_correspondence,
    )
```

`/records/{record_id}/open` (line 201):

```python
    result = await service.execute(
        slicer_url, open_script, context, request_timeout=60.0, include_correspondence=True
    )
```

`/records/{record_id}/validate` (line 282):

```python
    result = await service.execute(
        slicer_url, validator_script, context, request_timeout=60.0, include_correspondence=True
    )
```

`clarinet/api/routers/record.py:529`:

```python
        exec_result = await slicer_service.execute(
            slicer_url,
            validator_script,
            context,
            request_timeout=60.0,
            include_correspondence=True,
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_slicer_router_bundle.py tests/integration/test_record_submit_execresult.py -q > /tmp/test-slicer-engine-parity.txt 2>&1`
Expected: 4 + 10 passed.

- [ ] **Step 5: Commit**

```bash
git add clarinet/api/routers/slicer.py clarinet/api/routers/record.py tests/utils/urls.py tests/integration/test_slicer_router_bundle.py tests/integration/test_record_submit_execresult.py
git commit -m "feat(api): supply correspondence bundle on record script paths"
```

---

### Task 6: Live-Slicer coverage for the new parameters (tasks.md 3.3 additions)

**Files:**
- Modify: `tests/integration/test_slicer_helper.py` (two new tests after `test_subtract_segmentations_guards_grid_mismatch`, line 761)

Requires a running 3D Slicer; without one these skip (`_check_slicer`). Run with live Slicer if available; otherwise verify collection only and note it in the task report — Stage `test-all-stages` covers it later.

**Interfaces:**
- Consumes: final helper signature (Task 3); bundle symbols `ThresholdMatch`, `IoU` in the script namespace.

- [ ] **Step 1: Add the tests**

```python
async def test_subtract_strategy_override_live(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """An explicit strategy built from bundle symbols drives the verdict."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np
s = SlicerHelper(working_folder)
vol = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', '_StratVol')
slicer.util.updateVolumeFromArray(vol, np.zeros((20, 20, 20), dtype=np.int16))
s._image_node = vol
seg_logic = slicer.modules.segmentations.logic()


def _seg_from(name, slices):
    node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', name)
    node.CreateDefaultDisplayNodes()
    node.SetReferenceImageGeometryParameterFromVolumeNode(vol)
    lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', name + '_lm')
    arr = np.zeros((20, 20, 20), dtype=np.uint8)
    for value, sl in enumerate(slices, start=1):
        arr[sl] = value
    slicer.util.updateVolumeFromArray(lm, arr)
    seg_logic.ImportLabelmapToSegmentationNode(lm, node)
    slicer.mrmlScene.RemoveNode(lm)
    return node


# Base: one 4x4x4 blob. Other: identical blob -> IoU 1.0.
base = _seg_from('StratBase', [np.s_[5:9, 5:9, 5:9]])
other = _seg_from('StratOther', [np.s_[5:9, 5:9, 5:9]])

# Permissive scalar thresholds would keep nothing anyway; prove the strategy
# object wins by using one that only matches at IoU >= 0.9.
out = s.subtract_segmentations(
    base, other, output_name='_StratOut',
    strategy=ThresholdMatch(IoU(), min_score=0.9),
)
removed_by_strategy = out.GetSegmentation().GetNumberOfSegments()

# Same scene, disjoint other -> IoU 0 -> kept.
disjoint = _seg_from('StratDisjoint', [np.s_[15:18, 15:18, 15:18]])
out2 = s.subtract_segmentations(
    base, disjoint, output_name='_StratOut2',
    strategy=ThresholdMatch(IoU(), min_score=0.9),
)
kept_when_disjoint = out2.GetSegmentation().GetNumberOfSegments()

__execResult = {"removed_by_strategy": removed_by_strategy, "kept_when_disjoint": kept_when_disjoint}
"""
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )
    assert result["removed_by_strategy"] == 0
    assert result["kept_when_disjoint"] == 1


async def test_subtract_union_granularity_live(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Fragmented sub-threshold overlap: default keeps, union removes (D8)."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np
s = SlicerHelper(working_folder)
vol = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', '_GranVol')
slicer.util.updateVolumeFromArray(vol, np.zeros((20, 20, 20), dtype=np.int16))
s._image_node = vol
seg_logic = slicer.modules.segmentations.logic()


def _seg_from(name, slices):
    node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', name)
    node.CreateDefaultDisplayNodes()
    node.SetReferenceImageGeometryParameterFromVolumeNode(vol)
    lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', name + '_lm')
    arr = np.zeros((20, 20, 20), dtype=np.uint8)
    for value, sl in enumerate(slices, start=1):
        arr[sl] = value
    slicer.util.updateVolumeFromArray(lm, arr)
    seg_logic.ImportLabelmapToSegmentationNode(lm, node)
    slicer.mrmlScene.RemoveNode(lm)
    return node


# Base row of 10 voxels; two disjoint 3-voxel subtracted segments (0.3 each, 0.6 joint).
base = _seg_from('GranBase', [np.s_[5, 3:13, 5]])
frags = _seg_from('GranFrags', [np.s_[5, 3:6, 5], np.s_[5, 8:11, 5]])

out_label = s.subtract_segmentations(
    base, frags, output_name='_GranLabel', max_overlap_ratio=0.5,
)
out_union = s.subtract_segmentations(
    base, frags, output_name='_GranUnion', max_overlap_ratio=0.5, granularity='union',
)
__execResult = {
    "kept_default": out_label.GetSegmentation().GetNumberOfSegments(),
    "kept_union": out_union.GetSegmentation().GetNumberOfSegments(),
}
"""
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )
    assert result["kept_default"] == 1
    assert result["kept_union"] == 0
```

- [ ] **Step 2: Verify collection; run live if Slicer is up**

Run: `uv run pytest tests/integration/test_slicer_helper.py --collect-only -q`
Expected: 39 collected.
With live Slicer: `uv run pytest tests/integration/test_slicer_helper.py -q > /tmp/test-slicer-engine-parity.txt 2>&1` — expected all pass (else skip-clean).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_slicer_helper.py
git commit -m "test(slicer): live coverage for strategy override and union granularity"
```

---

### Task 7: Docs + final gates (tasks.md 6.1–6.3)

**Files:**
- Modify: `.claude/rules/slicer-helper-api.md` (subtract entry), then byte-copy to `clarinet/docs/slicer-helper-api.md`
- Modify: `clarinet/services/slicer/CLAUDE.md` (Router Endpoints `/exec` note + key-methods mention)

- [ ] **Step 1: Rewrite the `subtract_segmentations` entry** in `.claude/rules/slicer-helper-api.md` (currently the single line starting `- \`subtract_segmentations(seg_a, seg_b, output_name=None, ...)\``); replace with:

```markdown
- `subtract_segmentations(seg_a, seg_b, output_name=None, max_overlap=0, max_overlap_ratio=None, resample=False, *, strategy=None, granularity="label")` — ROI-level subtraction backed by the shared correspondence engine: removal verdicts run `correspond()` → `Difference()` → `KeepPlan`, identical to `Segmentation.difference`. Requires the correspondence bundle — raises `SlicerHelperError` otherwise, regardless of operand content (same session-sticky opt-in as `detect_overlaps`; record open/validate and submit-validator paths always include the bundle, ad-hoc `/exec` opts in via `include_correspondence`). Scalars derive the strategy via the bundled `strategy_from_thresholds`: ratio takes precedence over `max_overlap` and removal fires at `overlap/size >= max_overlap_ratio`; pass `strategy=` (built from bundle symbols, e.g. `ThresholdMatch(IoU(), min_score=0.5)`) for full control. `granularity="label"` (default) scores each subtracted segment separately; `"union"` flattens them into one mask (legacy sum-over-union). Verdict shifts vs the pre-engine helper: fragmented sub-threshold overlap (union restores it), exact ratio threshold (legacy `>` is now `>=`), both scalars set (ratio wins; legacy required both to be exceeded). Default `resample=False` raises on a source-vs-volume grid mismatch; `resample=True` re-grids runtime-natively (labelmap re-export here, `reindex_to` server-side) — near-threshold verdicts may differ between runtimes on that opt-in path. Empty sources stay warned no-ops
```

- [ ] **Step 2: Sync the golden copy**

```bash
cp .claude/rules/slicer-helper-api.md clarinet/docs/slicer-helper-api.md
```

- [ ] **Step 3: Update `clarinet/services/slicer/CLAUDE.md`** — in "Router Endpoints", extend the `POST /exec` bullet with: `optional include_correspondence: bool requests the correspondence-engine bundle; /records/{id}/open, /records/{id}/validate, and the submit-path validator always include it (D9, slicer-engine-parity)`. In "Key methods", after `detect_overlaps()`, add `subtract_segmentations()` if absent.

- [ ] **Step 4: Full gates**

```bash
make check
timeout 300 make test-fast > /tmp/test-slicer-engine-parity.txt 2>&1
```

Expected: check clean; test-fast green including `tests/test_agent_scaffold.py` (17 — byte-identity guard). After `make check`, re-`Read` any file you edit further — ruff format may have rewritten it.

- [ ] **Step 5: Commit**

```bash
git add .claude/rules/slicer-helper-api.md clarinet/docs/slicer-helper-api.md clarinet/services/slicer/CLAUDE.md
git commit -m "docs(slicer): document engine-backed subtract_segmentations"
```

---

## PR notes (for the eventual `gh pr create`)

Body must name the three verdict-shift cases (design.md → Risks; spec scenarios):
1. Fragmented sub-threshold overlap — several disjoint subtracted components each below the threshold but jointly above it now KEEP the base segment by default; `granularity="union"` restores removal.
2. Exact ratio threshold — legacy `overlap/total > max_overlap_ratio` is now `>=`.
3. Both scalars set — legacy removed only when both thresholds were exceeded; now the ratio wins and `max_overlap` is ignored (matches `Segmentation.difference`).

Downstream note (verified against nir_liver sources 2026-07-21): bundle availability is covered by D9 on all three call sites. Verdict exposure:
- `second_review.py:33` (`projection − doctor_seg`) and `:46` (`missed − classification`), both `max_overlap_ratio` at the shared 0.05 threshold — case 2 (`>=` boundary) applies, and **case 1 applies too**: seg_b is multi-segment at both sites (`classification` holds the four class segments by construction; `doctor_seg` may hold several segments), so an ROI fragment-covered by two segments each <5% but jointly ≥5% flips from removed (legacy sum-over-union) to kept. Direction is conservative — the ROI stays in the review pool instead of being silently dropped. To restore exact legacy granularity, pass `granularity="union"`; recommendation: keep the new default and note the shift.
- `update_master_model.py:77` (`doctor_islands − projection`, bare `max_overlap=0`) — unaffected: the absolute boundary is integer-exact under the `+1` derivation, and per-label vs union cannot diverge when any ≥1-voxel fragment already triggers removal.

## Self-review notes

- Spec coverage: single-engine path (T3), no-fallback raise (T3), parameter set + parity test (T2/T3/T4), shared derivation + bundle exposure (T1), granularity default/union/fragmented (T2/T6), grid guards unchanged (T3 keeps `_export_segments_labelmap`; live guard test opts in), empty-source no-ops (T3 keeps the `None`-array short-circuit; live `test_setop_tolerates_emptied_source` opts in), ad-hoc default-exclude + opt-in and record/validator paths (T5).
- Types: `strategy_from_thresholds` returns `ThresholdMatch` (satisfies `MatchingStrategy`); helper annotates `strategy: Any` (bundle types unavailable at runtime) — parity compares names, not annotations.
- `resample=True` union edge: flatten happens after export, so union granularity composes with either grid path.
