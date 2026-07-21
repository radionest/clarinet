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
        ns,
        arr_a,
        arr_b,
        max_overlap_ratio=0.5,
        strategy=ns["ThresholdMatch"](ns["IoU"](), min_score=0.99),
    )
    server = _server_keep_labels(
        arr_a,
        arr_b,
        max_overlap_ratio=0.5,
        strategy=ThresholdMatch(IoU(), min_score=0.99),
    )
    assert keep == server == {1, 2}


def test_engine_parameter_parity() -> None:
    """D10: one engine implies one parameter set — names must not drift apart.

    Runtime-only names are the documented exclusions: node handling on the
    Slicer side (operands + output_name), the operand on the image side.
    """
    import inspect

    sub = set(inspect.signature(SlicerHelper.subtract_segmentations).parameters)
    diff = set(inspect.signature(Segmentation.difference).parameters)
    assert sub - {"self", "seg_a", "seg_b", "output_name"} == diff - {"self", "other"}
