"""Segmentation class — labeled masks with morphology, set operations, and ROI filtering."""

from __future__ import annotations

import warnings
from typing import Any, Literal, Self

import numpy as np
from skimage.measure import label, regionprops
from skimage.measure._regionprops import _RegionProperties
from skimage.morphology import (
    ball,
    binary_opening,
    dilation,
    isotropic_dilation,
    isotropic_erosion,
    opening,
)

from clarinet.services.image.image import Image
from clarinet.utils.logger import logger

PropName = Literal["axis_major_length", "num_pixels", "area"]

# Z-extent threshold: below this, use isotropic (spacing-aware) morphology;
# above, fall back to ball structuring element for performance.
_ISOTROPIC_Z_THRESHOLD = 200


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

    @Image.img.setter  # type: ignore[misc, attr-defined, untyped-decorator]
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
        return new_seg

    def subtract(self, other: Self) -> None:
        """Zero out all voxels that are nonzero in `other` (in-place).

        Args:
            other: Segmentation to subtract.
        """
        other = self._align_other(other)  # type: ignore[assignment]
        img = self.img.copy()
        img[other.img != 0] = 0
        self.img = img

    def append(self, other: Self | Image) -> None:
        """Add ROIs from `other` that overlap with exactly one existing label.

        Each connected component in `other` is checked for overlap with this mask:
        - No overlap: skipped.
        - Overlaps one label: merged with that label value.
        - Overlaps multiple labels: raises ValueError.

        Args:
            other: Image or Segmentation whose ROIs to append.

        Raises:
            ValueError: If an ROI in `other` overlaps multiple labels.
        """
        other = self._align_other(other)  # type: ignore[assignment]
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

    def reindex_to(self, target: Image, *, order: int = 0) -> Segmentation:
        """Resample into *target*'s voxel grid (nearest-neighbor only).

        Overrides :meth:`Image.reindex_to` to force ``order=0``, preventing
        label value corruption from interpolation.
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
        return result

    def _align_other(self, other: Image) -> Image:
        """Return *other* reindexed to *self*'s grid, or unchanged if grids match."""
        if self._same_grid(other):
            return other
        return other.reindex_to(self, order=0)

    # ------------------------------------------------------------------
    # Named set operations (preferred API)
    # ------------------------------------------------------------------

    def intersection(
        self,
        other: Self,
        *,
        min_overlap: int = 1,
        min_overlap_ratio: float | None = None,
    ) -> Segmentation:
        """Intersection: keep ROIs from self that overlap sufficiently with other.

        Args:
            other: Segmentation to intersect with.
            min_overlap: Minimum absolute overlap in voxels to keep a label.
            min_overlap_ratio: Minimum overlap as a fraction of label size
                (0.0--1.0). ``None`` disables the ratio check.

        Returns:
            New Segmentation with only the kept labels.
        """
        other = self._align_other(other)  # type: ignore[assignment]
        output = Segmentation(template=self)
        for region in self.label_props:
            coords = region.coords
            overlap_mask = other.img[coords[:, 0], coords[:, 1], coords[:, 2]]
            overlap = int(np.sum(overlap_mask > 0))
            if overlap >= min_overlap:
                if min_overlap_ratio is not None:
                    total = int(np.sum(self.img == region.label))
                    if overlap / total < min_overlap_ratio:
                        continue
                output.img[coords[:, 0], coords[:, 1], coords[:, 2]] = region.label
        return output

    def union(self, other: Self) -> Segmentation:
        """Union: combine nonzero voxels from both masks into a binary result.

        Args:
            other: Segmentation to combine with.

        Returns:
            New Segmentation with all nonzero voxels from either mask.
        """
        if other.img.size == 1:
            return Segmentation(template=self, copy_data=True)
        other = self._align_other(other)  # type: ignore[assignment]
        output = Segmentation(template=self)
        combined = self.img.astype(np.uint16) + other.img.astype(np.uint16)
        combined[combined != 0] = 1
        output.img = combined
        return output

    def difference(
        self,
        other: Self,
        *,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
    ) -> Segmentation:
        """Difference with tolerance: keep ROIs with overlap below thresholds.

        A label is kept if its overlap with ``other`` is at most ``max_overlap``
        voxels AND (if ``max_overlap_ratio`` is set) the overlap ratio
        relative to the label's total voxels is below ``max_overlap_ratio``.

        Args:
            other: Segmentation to subtract.
            max_overlap: Maximum absolute overlap in voxels to tolerate.
            max_overlap_ratio: Maximum overlap as a fraction of label size
                (0.0--1.0). ``None`` disables the ratio check.

        Returns:
            New Segmentation with only the kept labels.
        """
        if other.img.size == 1:
            return Segmentation(template=self, copy_data=True)
        other = self._align_other(other)  # type: ignore[assignment]
        output = Segmentation(template=self)
        for region in self.label_props:
            coords = region.coords
            intersection = other.img[coords[:, 0], coords[:, 1], coords[:, 2]]
            overlap = int(np.sum(intersection > 0))
            if overlap <= max_overlap:
                if max_overlap_ratio is not None:
                    total = int(np.sum(self.img == region.label))
                    if overlap / total > max_overlap_ratio:
                        continue
                output.img[coords[:, 0], coords[:, 1], coords[:, 2]] = int(region.label)
        return output

    def symmetric_difference(
        self,
        other: Self,
        *,
        min_overlap: int = 1,
        min_overlap_ratio: float | None = None,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
    ) -> Segmentation:
        """Symmetric difference: voxels in the union but not in the intersection.

        Computes ``self.union(other).difference(self.intersection(other, ...), ...)``.

        Args:
            other: Segmentation to compare with.
            min_overlap: Passed to ``intersection()`` as ``min_overlap``.
            min_overlap_ratio: Passed to ``intersection()`` as ``min_overlap_ratio``.
            max_overlap: Passed to ``difference()`` as ``max_overlap``.
            max_overlap_ratio: Passed to ``difference()`` as ``max_overlap_ratio``.

        Returns:
            New Segmentation with the symmetric difference.
        """
        combined = self.union(other)
        intersected = self.intersection(
            other,
            min_overlap=min_overlap,
            min_overlap_ratio=min_overlap_ratio,
        )
        return combined.difference(
            intersected,
            max_overlap=max_overlap,
            max_overlap_ratio=max_overlap_ratio,
        )

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
