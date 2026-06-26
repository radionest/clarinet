"""Segmentation class — labeled masks with morphology, set operations, and ROI filtering."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Literal, Self

import numpy as np
from skimage.measure import label, regionprops
from skimage.measure._regionprops import _RegionProperties
from skimage.morphology import (  # type: ignore[attr-defined]
    ball,
    binary_opening,
    dilation,
    isotropic_dilation,
    isotropic_erosion,
    opening,
)

from clarinet.exceptions.domain import ImageError
from clarinet.services.image.correspondence import (
    AbsoluteOverlap,
    Coverage,
    MatchingStrategy,
    ThresholdMatch,
    correspond,
    render,
)
from clarinet.services.image.correspondence import (
    AppendMerge as _AppendMergeOp,
)
from clarinet.services.image.correspondence import (
    Difference as _DifferenceOp,
)
from clarinet.services.image.correspondence import (
    Intersection as _IntersectionOp,
)
from clarinet.services.image.correspondence import (
    SymmetricDifference as _SymmetricDifferenceOp,
)
from clarinet.services.image.image import FileType, Image, _is_segment_key
from clarinet.utils.logger import logger

PropName = Literal["axis_major_length", "num_pixels", "area"]

# Z-extent threshold: below this, use isotropic (spacing-aware) morphology;
# above, fall back to ball structuring element for performance.
_ISOTROPIC_Z_THRESHOLD = 200


def _strip_segment_metadata(header: dict[str, Any] | None) -> dict[str, Any]:
    """Header copy without any per-segment / ``Segmentation_*`` keys.

    Used by operations that relabel the mask (``union``, ``filter_segmentation``):
    their output labels are connected-component indices that no longer map to the
    source's named segments, so carrying those names would be wrong.
    """
    if not header:
        return {}
    return {k: v for k, v in header.items() if not _is_segment_key(k)}


class Segmentation(Image):
    """Labeled segmentation mask with morphological operations.

    Extends Image with automatic labeling, connected-component analysis,
    morphological operations, HU-based correction, ROI filtering, and
    set operations (intersection, union, difference, etc.).

    Args:
        autolabel: If True, auto-label connected components on each img assignment.
        template: Existing Image to copy metadata/shape from.
        copy_data: If True and template is given, copy voxel data.
    """

    def __init__(
        self,
        autolabel: bool = True,
        template: Any = None,
        copy_data: bool = False,
    ) -> None:
        self.autolabel = autolabel
        self._region_props: list[_RegionProperties] | None = None
        super().__init__(template=template, copy_data=copy_data, dtype=np.uint8)

    @Image.img.setter  # type: ignore[attr-defined,untyped-decorator]
    def img(self, vol: np.ndarray) -> None:
        if self.autolabel:
            self._img = label(vol).astype(np.uint8)
        else:
            self._img = vol.astype(np.uint8)
        self._region_props = None

    @property
    def label_props(self) -> list[_RegionProperties]:
        """Cached region properties for all labels."""
        if self._region_props is None:
            self._region_props = regionprops(self.img, spacing=self.spacing)
        return self._region_props

    @property
    def count(self) -> int:
        """Number of labeled regions."""
        return len(self.label_props)

    @property
    def is_empty(self) -> bool:
        """True if the mask contains no nonzero voxels."""
        return bool(np.all(self._img == 0))

    def separate_labels(self) -> None:
        """Re-label connected components in the current mask."""
        self.img = label(self._img)
        self._region_props = None

    def dilate(self, radius: int) -> None:
        """Dilate the binary mask.

        Args:
            radius: Dilation radius in voxels.
        """
        if self.img.shape[-1] < _ISOTROPIC_Z_THRESHOLD:
            self.img = isotropic_dilation(self.img > 0, radius, spacing=self.spacing)
        else:
            self.img = dilation(self.img > 0, footprint=ball(radius))

    def binary_open(self, radius: int) -> None:
        """Apply morphological opening (erosion + dilation) to the binary mask.

        Args:
            radius: Structuring element radius in voxels.
        """
        if self.img.shape[-1] < _ISOTROPIC_Z_THRESHOLD:
            self.img = isotropic_erosion(self.img > 0, radius, spacing=self.spacing)
            self.img = isotropic_dilation(self.img > 0, radius, spacing=self.spacing)
        else:
            self.img = binary_opening(image=self.img > 0, footprint=ball(radius))

    def rois_hu_correction(
        self,
        hu_image: Image,
        min_hu: float,
        max_hu: float,
        radius: int = 5,
        white_mask: Self | None = None,
    ) -> None:
        """Correct ROIs using HU range filtering.

        Dilates each ROI, optionally constrains to a white_mask, filters by HU range,
        applies opening, and keeps only the largest connected component per label.

        Args:
            hu_image: Source CT image with Hounsfield Unit values.
            min_hu: Minimum HU threshold.
            max_hu: Maximum HU threshold.
            radius: Dilation radius for ROI expansion.
            white_mask: Optional mask to constrain the dilated ROIs.
        """
        temp_img = dilation(self.img, footprint=ball(radius))
        if white_mask is not None:
            temp_img[white_mask.img == 0] = 0
            temp_img = np.where(self.img != 0, self.img, temp_img)
        temp_img[hu_image.img < min_hu] = 0
        temp_img[hu_image.img > max_hu] = 0
        temp_img = opening(temp_img, footprint=ball(2))

        for lbl in np.unique(temp_img)[1:]:
            roi_img = label(temp_img == lbl)
            temp_props = regionprops(roi_img)
            temp_props.sort(key=lambda p: p["area"], reverse=True)
            coords = temp_props[0].coords
            temp_img[temp_img == lbl] = 0
            temp_img[coords[:, 0], coords[:, 1], coords[:, 2]] = lbl
        self.img = temp_img

    def filtered_props(
        self,
        prop_name: PropName,
        ge: float | int = -float("inf"),
        le: float | int = float("inf"),
    ) -> list[_RegionProperties]:
        """Return region properties filtered by a numeric property range.

        Args:
            prop_name: Property name to filter on.
            ge: Minimum value (inclusive).
            le: Maximum value (inclusive).

        Returns:
            List of region properties within the specified range.
        """
        return [p for p in self.label_props if ge <= getattr(p, prop_name) <= le]

    def filter_roi(
        self,
        prop_name: PropName,
        ge: float | int = 0,
        le: float | int = float("inf"),
    ) -> np.ndarray:
        """Return a binary mask of ROIs matching the property filter.

        Args:
            prop_name: Property name to filter on.
            ge: Minimum value (inclusive).
            le: Maximum value (inclusive).

        Returns:
            Binary numpy array with matching ROIs set to 1.
        """
        filtered_rois = self.filtered_props(prop_name=prop_name, ge=ge, le=le)
        new_img = np.zeros(self.img.shape, dtype=np.uint8)
        for roi in filtered_rois:
            new_img[roi.coords[:, 0], roi.coords[:, 1], roi.coords[:, 2]] = 1
        return new_img

    def filter_segmentation(
        self,
        prop_name: PropName,
        ge: float | int = 0,
        le: float | int = float("inf"),
    ) -> Segmentation:
        """Return a new Segmentation containing only ROIs matching the property filter.

        Args:
            prop_name: Property name to filter on.
            ge: Minimum value (inclusive).
            le: Maximum value (inclusive).

        Returns:
            New Segmentation with only matching ROIs.
        """
        new_img = self.filter_roi(prop_name=prop_name, ge=ge, le=le)
        new_seg = Segmentation(template=self)
        new_seg.img = new_img
        new_seg._nrrd_header = _strip_segment_metadata(new_seg._nrrd_header)
        return new_seg

    def subtract(self, other: Self, *, resample: bool = False) -> None:
        """Zero out all voxels that are nonzero in `other` (in-place).

        Args:
            other: Segmentation to subtract.
            resample: If True, resample `other` onto this grid when they differ.
                If False (default), raises ``GeometryMismatchError`` on grid mismatch.
        """
        other = self._align_other(other, resample=resample)  # type: ignore[assignment]
        img = self.img.copy()
        img[other.img != 0] = 0
        self.img = img
        # In-place ops leave _nrrd_header untouched: a label fully removed here is
        # pruned from the segment metadata on write (Image._save_nrrd reconciles it).

    def append(
        self,
        other: Self | Image,
        *,
        strategy: MatchingStrategy | None = None,
        resample: bool = False,
    ) -> None:
        """Add ROIs from `other` that overlap with exactly one existing label.

        Each connected component in `other` is checked for overlap with this mask:
        - No overlap: skipped.
        - Overlaps one label: merged with that label value.
        - Overlaps multiple labels: raises ValueError (unless ``strategy`` is set).

        When ``strategy`` is provided, multi-label overlaps are resolved instead
        of raising: each B component is matched to its winning A label via the
        correspondence engine, and its voxels are repainted with that label value.

        Note:
            With ``strategy=``, each distinct label value in ``other`` is treated
            as one component (the correspondence engine keys on label values), so for
            connected-component granularity pass an autolabeled ``Segmentation``.

        Args:
            other: Image or Segmentation whose ROIs to append.
            strategy: Optional ``MatchingStrategy`` (e.g.
                ``GreedyArgmax(AbsoluteOverlap(), direction="b_to_a")``).
                When given, resolves multi-label overlaps via the correspondence
                engine rather than raising ``ValueError``.
            resample: If True, resample `other` onto this grid when they differ.
                If False (default), raises ``GeometryMismatchError`` on grid mismatch.

        Raises:
            ValueError: If an ROI in `other` overlaps multiple labels and no
                ``strategy`` is provided.
        """
        other = self._align_other(other, resample=resample)
        if strategy is not None:
            corr = correspond(self.img, other.img, spacing=self.spacing, strategy=strategy)
            merged = render(
                _AppendMergeOp()(corr), self.img, other.img, base=self.img, relabel=False
            )
            prev, self.autolabel = self.autolabel, False
            try:
                self.img = merged
            finally:
                self.autolabel = prev
            return
        for region in regionprops(label(other.img)):
            coords = region.coords
            intersection = self.img[coords[:, 0], coords[:, 1], coords[:, 2]]
            unique_labels = [int(v) for v in np.unique(intersection) if v != 0]

            match unique_labels:
                case []:
                    pass  # No overlap — skip
                case [label_value]:
                    self.img[coords[:, 0], coords[:, 1], coords[:, 2]] = label_value
                case [*label_values]:
                    raise ValueError(f"ROI overlaps multiple labels: {label_values}")

    def copy_from(self, other: Self) -> None:
        """Replace this mask's voxel data with data from `other`.

        Args:
            other: Source segmentation to copy from.
        """
        self.img = other.img

    def reindex_to(self, target: Image, *, order: Literal[0, 1] = 0) -> Segmentation:
        """Resample into *target*'s voxel grid (nearest-neighbor only).

        Overrides :meth:`Image.reindex_to` to force ``order=0``, preventing
        label value corruption from interpolation. Segment metadata (names, label
        values, colors) is carried onto the new grid, pruned to surviving labels.
        """
        if order != 0:
            logger.warning("Segmentation.reindex_to: forcing order=0 to prevent label corruption")
        from scipy.ndimage import affine_transform

        mapping = np.linalg.inv(self.affine_4x4) @ target.affine_4x4
        resampled = affine_transform(
            self.img,
            mapping[:3, :3],
            offset=mapping[:3, 3],
            output_shape=target.shape,
            order=0,
            mode="constant",
            cval=0,
        )
        result = Segmentation(autolabel=False, template=target)
        result.img = resampled
        # The template copies the *target*'s header (no segment metadata); carry the
        # source's segment keys instead. Save reconciles them to the surviving labels
        # and drops the now-stale grid extents.
        if self._nrrd_header is not None:
            seg_meta = {k: v for k, v in self._nrrd_header.items() if _is_segment_key(k)}
            result._nrrd_header = {**_strip_segment_metadata(result._nrrd_header), **seg_meta}
        return result

    def _align_other(self, other: Image, *, resample: bool = False) -> Image:
        """Return *other* on *self*'s grid.

        If the grids already match, returns *other* unchanged. Otherwise:

        - ``resample=False`` (default): raises :class:`GeometryMismatchError` — two
          segmentations compared by index must share a grid (cf. ITK). This is the
          safe default: a silent resample can mask a projection/segmentation that
          drifted onto a flipped grid.
        - ``resample=True``: resamples *other* onto *self*'s grid (nearest-neighbour,
          labels preserved).
        """
        if not resample:
            self.assert_same_grid(other)
            return other
        if self.same_grid(other):
            return other
        return other.reindex_to(self, order=0)

    # ------------------------------------------------------------------
    # Named set operations (preferred API)
    # ------------------------------------------------------------------

    def intersection(
        self,
        other: Self,
        *,
        strategy: MatchingStrategy | None = None,
        min_overlap: int = 1,
        min_overlap_ratio: float | None = None,
        resample: bool = False,
    ) -> Segmentation:
        """Intersection: keep ROIs from self that overlap sufficiently with other.

        Args:
            other: Segmentation to intersect with.
            strategy: Matching strategy override. When ``None``, defaults to
                ``ThresholdMatch(AbsoluteOverlap(), min_score=min_overlap)`` or
                ``ThresholdMatch(Coverage("a"), min_score=min_overlap_ratio)`` when
                ``min_overlap_ratio`` is given.
            min_overlap: Minimum absolute overlap in voxels to keep a label.
                Ignored when ``strategy`` or ``min_overlap_ratio`` is set.
            min_overlap_ratio: Minimum coverage (inter/size_a) to keep a label.
                When set, takes precedence over ``min_overlap``. ``None`` disables
                the ratio check.
            resample: If True, resample ``other`` onto this grid when they differ.
                If False (default), raises ``GeometryMismatchError`` on grid mismatch.

        Note:
            Without ``strategy=``, ``min_overlap`` is checked per other-component
            (largest single overlap), not the sum. Default (min_overlap=1) and
            single-component cases match old behavior; raised thresholds with
            fragmented overlap differ. Pass ``strategy=`` for full control.

        Returns:
            New Segmentation with only the kept labels.
        """
        if other.img.size == 1:
            return Segmentation(template=self)  # empty other → empty intersection
        other = self._align_other(other, resample=resample)  # type: ignore[assignment]
        if strategy is None:
            strategy = self._threshold(min_overlap, min_overlap_ratio)
        corr = correspond(self.img, other.img, spacing=self.spacing, strategy=strategy)
        out = Segmentation(autolabel=False, template=self)
        out.img = render(_IntersectionOp()(corr), self.img, other.img, relabel=False)
        return out

    def union(self, other: Self, *, resample: bool = False) -> Segmentation:
        """Union: combine nonzero voxels from both masks into a binary result.

        Args:
            other: Segmentation to combine with.
            resample: If True, resample `other` onto this grid when they differ.
                If False (default), raises ``GeometryMismatchError`` on grid mismatch.

        Returns:
            New Segmentation with all nonzero voxels from either mask.
        """
        if other.img.size == 1:
            return Segmentation(template=self, copy_data=True)
        other = self._align_other(other, resample=resample)  # type: ignore[assignment]
        output = Segmentation(template=self)
        combined = self.img.astype(np.uint16) + other.img.astype(np.uint16)
        combined[combined != 0] = 1
        output.img = combined
        # union relabels into connected components — the source's named segments no
        # longer map to these labels, so drop them (a plain labelmap is written).
        output._nrrd_header = _strip_segment_metadata(output._nrrd_header)
        return output

    def difference(
        self,
        other: Self,
        *,
        strategy: MatchingStrategy | None = None,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
        resample: bool = False,
    ) -> Segmentation:
        """Difference with tolerance: keep ROIs from self not sufficiently overlapping other.

        A label is kept when it is *unmatched* under the strategy. Default strategy:
        ``ThresholdMatch(AbsoluteOverlap(), min_score=max_overlap + 1)`` — keeps labels
        whose largest single-component overlap is at most ``max_overlap``. When
        ``max_overlap_ratio`` is provided the ratio takes precedence: a label is
        removed iff ``inter / size_a >= max_overlap_ratio`` (``Coverage("a")`` measure).

        Args:
            other: Segmentation to subtract.
            strategy: Matching strategy override. When ``None``, the default is
                derived from ``max_overlap`` / ``max_overlap_ratio``.
            max_overlap: Maximum absolute overlap in voxels to tolerate.
                Ignored when ``strategy`` or ``max_overlap_ratio`` is set.
            max_overlap_ratio: Maximum coverage (inter/size_a) to tolerate.
                When set, takes precedence over ``max_overlap``. ``None`` disables
                the ratio check.
            resample: If True, resample ``other`` onto this grid when they differ.
                If False (default), raises ``GeometryMismatchError`` on grid mismatch.

        Note:
            Without ``strategy=``, ``max_overlap`` is checked per other-component
            (largest single overlap), not the sum. Default (max_overlap=0) and
            single-component cases match old behavior; raised thresholds with
            fragmented overlap differ. Pass ``strategy=`` for full control.

        Returns:
            New Segmentation with only the kept labels.
        """
        if other.img.size == 1:
            return Segmentation(template=self, copy_data=True)
        other = self._align_other(other, resample=resample)  # type: ignore[assignment]
        if strategy is None:
            if max_overlap_ratio is not None:
                strategy = ThresholdMatch(Coverage("a"), min_score=max_overlap_ratio)
            else:
                strategy = ThresholdMatch(AbsoluteOverlap(), min_score=float(max_overlap + 1))
        corr = correspond(self.img, other.img, spacing=self.spacing, strategy=strategy)
        out = Segmentation(autolabel=False, template=self)
        out.img = render(_DifferenceOp()(corr), self.img, other.img, relabel=False)
        return out

    def symmetric_difference(
        self,
        other: Self,
        *,
        strategy: MatchingStrategy | None = None,
        min_overlap: int = 1,
        min_overlap_ratio: float | None = None,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
        resample: bool = False,
    ) -> Segmentation:
        """Symmetric difference: component-level labels unique to each side.

        Uses the correspondence engine: unmatched A labels and unmatched B labels
        are combined into a single relabeled output. This is a cleaner decomposition
        than the legacy union-minus-intersection approach.

        ``max_overlap`` and ``max_overlap_ratio`` are accepted for API stability but
        are ignored — use ``min_overlap`` / ``min_overlap_ratio`` or ``strategy=``
        to control the matching threshold.

        Args:
            other: Segmentation to compare with.
            strategy: Matching strategy override. When ``None``, defaults to
                ``ThresholdMatch(AbsoluteOverlap(), min_score=min_overlap)`` or
                ``ThresholdMatch(Coverage("a"), min_score=min_overlap_ratio)`` when
                ``min_overlap_ratio`` is given.
            min_overlap: Minimum absolute overlap to consider a pair matched.
                Ignored when ``strategy`` or ``min_overlap_ratio`` is set.
            min_overlap_ratio: Minimum coverage (inter/size_a) to match.
                When set, takes precedence over ``min_overlap``.
            max_overlap: Accepted for API stability; ignored.
            max_overlap_ratio: Accepted for API stability; ignored.
            resample: If True, resample ``other`` onto this grid when they differ.
                If False (default), raises ``GeometryMismatchError`` on grid mismatch.

        Returns:
            New Segmentation with the symmetric difference.
        """
        if max_overlap != 0 or max_overlap_ratio is not None:
            logger.warning(
                "Segmentation.symmetric_difference: max_overlap/max_overlap_ratio are "
                "ignored in the correspondence model; use min_overlap/min_overlap_ratio "
                "or strategy= instead."
            )
        if other.img.size == 1:
            return Segmentation(template=self, copy_data=True)
        other = self._align_other(other, resample=resample)  # type: ignore[assignment]
        if strategy is None:
            strategy = self._threshold(min_overlap, min_overlap_ratio)
        corr = correspond(self.img, other.img, spacing=self.spacing, strategy=strategy)
        out = Segmentation(autolabel=False, template=self)
        out.img = render(_SymmetricDifferenceOp()(corr), self.img, other.img, relabel=True)
        return out

    @staticmethod
    def _threshold(min_overlap: int, min_overlap_ratio: float | None) -> MatchingStrategy:
        """Build the default matching strategy from scalar thresholds."""
        if min_overlap_ratio is not None:
            return ThresholdMatch(Coverage("a"), min_score=min_overlap_ratio)
        return ThresholdMatch(AbsoluteOverlap(), min_score=float(min_overlap))

    # ------------------------------------------------------------------
    # Deprecated operators — delegate to named methods
    # ------------------------------------------------------------------

    def __and__(self, other: Self) -> Segmentation:
        """Intersection: keep ROIs from self that overlap with other.

        .. deprecated::
            Use ``intersection()`` for configurable overlap thresholds.
        """
        warnings.warn(
            "Segmentation.__and__ (& operator) is deprecated. "
            "Use .intersection(other, min_overlap=...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.intersection(other, min_overlap=3)

    def __or__(self, other: Self) -> Segmentation:
        """Union: combine nonzero voxels from both masks.

        .. deprecated::
            Use ``union()`` instead.
        """
        warnings.warn(
            "Segmentation.__or__ (| operator) is deprecated. Use .union(other) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.union(other)

    def __sub__(self, other: Self) -> Segmentation:
        """Difference: keep ROIs from self that do NOT overlap with other.

        .. deprecated::
            Use ``difference()`` for configurable overlap thresholds.
        """
        warnings.warn(
            "Segmentation.__sub__ (- operator) is deprecated. "
            "Use .difference(other, max_overlap=...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.difference(other)

    def __add__(self, other: Self) -> Segmentation:
        """Add: merge nonzero voxels from both masks (binary union).

        .. deprecated::
            Use ``union()`` instead.
        """
        warnings.warn(
            "Segmentation.__add__ (+ operator) is deprecated. Use .union(other) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.union(other)

    def __xor__(self, other: Self) -> Segmentation:
        """Symmetric difference: union minus intersection.

        .. deprecated::
            Use ``symmetric_difference()`` for configurable overlap thresholds.
        """
        warnings.warn(
            "Segmentation.__xor__ (^ operator) is deprecated. "
            "Use .symmetric_difference(other, ...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.symmetric_difference(other, min_overlap=3)


def conform_seg_to_grid(
    seg_path: Path | str,
    grid_path: Path | str,
    *,
    out_path: Path | str | None = None,
    atol: float = 1e-4,
) -> bool:
    """Repair a segmentation file so it shares *grid_path*'s voxel grid.

    Reads the segmentation and a reference image (e.g. the series ``volume.nii.gz``);
    if their grids differ, resamples the segmentation onto the reference grid
    (nearest-neighbour, labels preserved) and writes it back. A no-op when the grids
    already match.

    Intended for downstream repair scripts that fix historically misaligned
    ``.seg.nrrd`` files (e.g. projection/segmentation Z-flips) against the canonical
    series volume.

    Args:
        seg_path: Segmentation file to repair.
        grid_path: Reference image whose grid is the target (``volume.nii.gz`` / .nrrd).
        out_path: Where to write the conformed segmentation. Defaults to *seg_path*
            (in-place overwrite).
        atol: Grid-equality tolerance passed to :meth:`Image.same_grid`.

    Returns:
        True if a resample was performed, False if the grids already matched.

    Raises:
        ImageError: If *out_path* has an unsupported extension.
    """
    # Resolve target format up front so an unsupported extension fails before
    # reading two images off disk.
    target = Path(out_path) if out_path is not None else Path(seg_path)
    suffixes = target.suffixes
    if ".nrrd" in suffixes:
        filetype = FileType.NRRD
    elif ".nii" in suffixes:
        filetype = FileType.NIFTI
    else:
        raise ImageError(f"Cannot infer format for {target.name}: expected .nrrd, .nii, or .nii.gz")

    seg = Segmentation(autolabel=False)
    seg.read(Path(seg_path))
    reference = Image()
    reference.read(Path(grid_path))

    if seg.same_grid(reference, atol=atol):
        return False

    conformed = seg.reindex_to(reference, order=0)
    conformed.save_as(target, filetype)
    logger.info(f"Conformed segmentation {Path(seg_path).name} onto grid of {Path(grid_path).name}")
    return True
