"""Image class for reading/writing images (NIfTI, NRRD, DICOM)."""

from __future__ import annotations

import enum
import re
from pathlib import Path
from typing import Any, Literal, Self

import nibabel
import nibabel.affines
import nibabel.loadsave
import nrrd
import numpy as np

from clarinet.exceptions.domain import (
    GeometryMismatchError,
    ImageError,
    ImageReadError,
    ImageWriteError,
)
from clarinet.services.image.grid import Grid
from clarinet.utils.logger import logger

# Internal representation uses LPS (DICOM native). NIfTI uses RAS.
# Flip X and Y to convert between them (the matrix is its own inverse).
_LPS_TO_RAS = np.diag([-1.0, -1.0, 1.0])
# LAS differs from LPS only in the y axis (Anterior vs Posterior); also self-inverse.
_LAS_TO_LPS = np.diag([1.0, -1.0, 1.0])

_NRRD_SPACE_LPS = frozenset({"left-posterior-superior", "lps"})
_NRRD_SPACE_RAS = frozenset({"right-anterior-superior", "ras"})
_NRRD_SPACE_LAS = frozenset({"left-anterior-superior", "las"})


def _nrrd_space_to_lps(
    space: str | None,
    space_directions: np.ndarray,
    space_origin: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Convert NRRD ``space directions``/``space origin`` into clarinet's internal LPS.

    Honors the header's ``space`` field (case-insensitive; full name or abbreviation,
    e.g. ``"right-anterior-superior"`` or ``"RAS"``). LPS passes through unchanged;
    RAS/LAS are converted by negating the affected world components of every direction
    row and of the origin (same transform for both — they express vectors/points in the
    same declared coordinate system). Slicer always writes LPS (probe P6) — this only
    affects third-party files.

    Shared by :meth:`Image.read_nrrd` and
    :meth:`~clarinet.services.image.layered_segmentation.LayeredSegmentation._apply_grid_from_header`;
    callers pre-slice ``space_directions`` to the 3 spatial rows (a 4-D layered header's
    row 0 is the ``none`` list axis, not a spatial direction).

    Args:
        space: The header's ``space`` field (``None`` if the header omits it).
        space_directions: ``(3, 3)`` array; each row is one axis's world-space
            direction vector (spacing baked in), expressed in the header's ``space``.
        space_origin: ``(3,)`` world-space origin in the header's ``space``, or
            ``None`` if the header has no ``space origin``.

    Returns:
        ``(space_directions, space_origin)`` re-expressed in LPS. The second element
        is ``None`` iff *space_origin* was ``None``.

    Raises:
        ImageReadError: ``space`` is missing or not one of LPS/RAS/LAS.
    """
    normalized = (space or "").strip().lower()
    if normalized in _NRRD_SPACE_LPS:
        return space_directions, space_origin
    if normalized in _NRRD_SPACE_RAS:
        transform = _LPS_TO_RAS  # self-inverse: also converts RAS -> LPS
    elif normalized in _NRRD_SPACE_LAS:
        transform = _LAS_TO_LPS
    else:
        raise ImageReadError(
            f"Unsupported NRRD space {space!r}: expected left-posterior-superior/LPS, "
            "right-anterior-superior/RAS, or left-anterior-superior/LAS"
        )
    # Each space_directions row is a per-axis world vector [x, y, z]; post-multiplying
    # by the (diagonal) transform scales those world x/y/z *columns*, i.e. negates the
    # same components in every row. space_origin is a single such vector, pre-multiplied
    # for the conventional matrix-vector form (equivalent for a diagonal transform).
    dirs_lps = space_directions @ transform
    origin_lps = None if space_origin is None else transform @ space_origin
    return dirs_lps, origin_lps


# Matches a per-segment NRRD header key, e.g. "Segment0_Name" -> ("0", "Name").
_SEGMENT_BLOCK_KEY = re.compile(r"^Segment(\d+)_(.+)$")
# Segment metadata encoding the voxel grid: invalidated by any resample. Matched
# exactly (per-segment sub-key / global key) rather than by substring.
_GRID_DEPENDENT_SEGMENT_SUBKEYS = frozenset({"Extent"})
_GRID_DEPENDENT_SEGMENT_GLOBALS = frozenset({"Segmentation_ReferenceImageExtentOffset"})


def _is_segment_key(key: str) -> bool:
    """True for any NRRD segmentation header key (per-segment block or global)."""
    return key.startswith("Segment")


def _is_grid_dependent_segment_key(key: str) -> bool:
    """True for segment-header keys tied to a specific voxel grid (dropped on write).

    Slicer stores per-segment voxel bounding boxes (``Segment{i}_Extent``) and a
    global ``Segmentation_ReferenceImageExtentOffset``; both become invalid once the
    grid changes (resample / reindex), so readers recompute the effective extent
    from the labelmap on load. Matched exactly — not by ``"Extent" in key`` — so a
    semantic key that merely contains the substring is never dropped by accident.
    The semantic keys (``_Name``, ``_LabelValue``, ``_Color``, ``_Layer``, ...) are
    grid-independent and must round-trip.
    """
    if key in _GRID_DEPENDENT_SEGMENT_GLOBALS:
        return True
    match = _SEGMENT_BLOCK_KEY.match(key)
    return match is not None and match.group(2) in _GRID_DEPENDENT_SEGMENT_SUBKEYS


def _present_labels(volume: np.ndarray) -> set[int]:
    """Nonzero label values present in a labelmap."""
    return {int(v) for v in np.unique(volume) if v != 0}


def _reconcile_segment_metadata(header: dict[str, Any], present_labels: set[int]) -> dict[str, Any]:
    """Return only the segment header keys that match the labels actually present.

    - per-segment blocks (``Segment{i}_*``) are kept iff their ``LabelValue`` is in
      ``present_labels``, then renumbered contiguously from 0;
    - grid-dependent keys (``*_Extent``) are dropped;
    - global ``Segmentation_*`` keys are preserved (minus the grid-dependent offset).

    Applied on write so a saved segmentation never names a label value absent from
    its voxel data (e.g. after a set operation or resample drops a label).
    """
    blocks: dict[int, dict[str, Any]] = {}
    seg_globals: dict[str, Any] = {}
    for key, value in header.items():
        match = _SEGMENT_BLOCK_KEY.match(key)
        if match is not None:
            if not _is_grid_dependent_segment_key(key):
                blocks.setdefault(int(match.group(1)), {})[match.group(2)] = value
        elif _is_segment_key(key) and not _is_grid_dependent_segment_key(key):
            seg_globals[key] = value

    reconciled: dict[str, Any] = dict(seg_globals)
    emitted_labels: set[int] = set()
    new_index = 0
    for old_index in sorted(blocks):
        block = blocks[old_index]
        try:
            label_value = int(block["LabelValue"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                f"Dropping segment block {old_index} with missing/invalid LabelValue "
                f"{block.get('LabelValue')!r} (corrupted segmentation metadata?)"
            )
            continue
        if label_value not in present_labels:
            continue
        if label_value in emitted_labels:
            logger.warning(
                f"Duplicate segment LabelValue {label_value} in NRRD header; "
                "keeping both blocks (corrupted segmentation metadata?)"
            )
        emitted_labels.add(label_value)
        for sub_key, value in block.items():
            reconciled[f"Segment{new_index}_{sub_key}"] = value
        new_index += 1
    return reconciled


class FileType(enum.Enum):
    """Supported image file formats."""

    NIFTI = "nifti"
    NRRD = "nrrd"
    DICOM = "dicom"


class Image:
    """3D image with format-aware I/O.

    Supports NIfTI (.nii, .nii.gz), NRRD (.nrrd), and DICOM series reading.
    Provides unified access to voxel data, spacing, and shape regardless of
    the underlying file format.

    Args:
        template: Existing Image to copy metadata/shape from.
        copy_data: If True and template is given, copy voxel data instead of zeros.
        dtype: Force voxel data to this numpy dtype on assignment.
    """

    def __init__(
        self,
        template: Self | None = None,
        copy_data: bool = False,
        dtype: Any = None,
    ) -> None:
        self._img: np.ndarray | None = None
        self._spacing: tuple[float, float, float] = (1.0, 1.0, 1.0)
        self._origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._direction: np.ndarray = np.eye(3)
        self._source_path: Path | None = None
        self._filetype: FileType | None = None
        self._nifti_image: Any = None
        self._nrrd_header: dict[str, Any] | None = None
        self._shape: tuple[int, ...] | None = None
        self.force_dtype: Any = dtype

        if template is not None:
            self._source_path = template._source_path
            self.spacing = template.spacing
            self._origin = template._origin
            self._direction = template._direction.copy()
            # NOT inherited: a derived image's `img` holds freshly-computed voxels, not
            # the template's on-disk data — copying the template's lazy proxy would make
            # `dataobj` return stale source voxels. `_nifti_image` stays None (set above).
            self._nrrd_header = getattr(template, "_nrrd_header", None)
            self._filetype = template._filetype
            self._shape = template._shape
            if copy_data:
                self.img = np.copy(template.img)
            else:
                if self.force_dtype is not None:
                    zeros_dtype: Any = self.force_dtype
                elif template.has_data:
                    zeros_dtype = template.img.dtype
                else:
                    zeros_dtype = np.float64  # legacy default for a metadata-only base template
                self.img = np.zeros(template.shape, dtype=zeros_dtype)

    @property
    def img(self) -> np.ndarray:
        """Voxel data as a numpy array."""
        if self._img is None:
            raise ImageError("Image data is not loaded")
        return self._img

    @img.setter
    def img(self, vol: np.ndarray) -> None:
        if self.force_dtype is not None:
            # asarray returns vol unchanged when the dtype already matches (no copy);
            # casts once otherwise. Prevents a redundant copy of an already-correct array.
            self._img = np.asarray(vol, dtype=self.force_dtype)
        else:
            self._img = vol

    @property
    def spacing(self) -> tuple[float, float, float]:
        """Voxel spacing in mm (x, y, z)."""
        return self._spacing

    @spacing.setter
    def spacing(self, values: tuple[float, float, float]) -> None:
        if len(values) != 3:
            raise ValueError(f"Spacing must be a 3-tuple, got length {len(values)}")
        self._spacing = (float(values[0]), float(values[1]), float(values[2]))

    @property
    def origin(self) -> tuple[float, float, float]:
        """Patient-space origin (x, y, z) in mm."""
        return self._origin

    @origin.setter
    def origin(self, values: tuple[float, float, float]) -> None:
        if len(values) != 3:
            raise ValueError(f"Origin must be a 3-tuple, got length {len(values)}")
        self._origin = (float(values[0]), float(values[1]), float(values[2]))

    @property
    def direction(self) -> np.ndarray:
        """3x3 direction cosine matrix (columns = unit direction vectors per axis)."""
        return self._direction

    @direction.setter
    def direction(self, value: np.ndarray) -> None:
        arr = np.asarray(value, dtype=float)
        if arr.shape != (3, 3):
            raise ValueError(f"Direction must be a 3x3 matrix, got shape {arr.shape}")
        self._direction = arr

    @property
    def shape(self) -> tuple[int, ...]:
        """Shape of the voxel array.

        Works on a metadata-only image (``read(..., load_data=False)``): returns the
        header shape when voxels are not loaded. Raises if neither data nor a header
        has been read.
        """
        if self._img is not None:
            return tuple(self._img.shape)
        if self._shape is not None:
            return tuple(self._shape)
        raise ImageError("Image shape is unavailable: no data loaded and no header read")

    @property
    def has_data(self) -> bool:
        """True when voxel data is resident (as opposed to a metadata-only read)."""
        return self._img is not None

    @property
    def dataobj(self) -> Any:
        """Read-only lazy array proxy for repeated windowed reads (NIfTI only).

        For callers that window after ``read(..., load_data=False)`` (e.g. a streaming
        nonzero check). NIfTI only — raises ``ImageError`` for NRRD/DICOM, which have no
        lazy proxy (pynrrd reads the whole array).
        """
        if self._filetype == FileType.NIFTI and self._nifti_image is not None:
            return self._nifti_image.dataobj
        label = self._filetype.value if self._filetype is not None else "unloaded image"
        raise ImageError(f"no lazy proxy for {label}")

    @property
    def affine_4x4(self) -> np.ndarray:
        """4x4 voxel-to-physical affine matrix in LPS coordinates."""
        A = np.eye(4)
        A[:3, :3] = self._direction * np.array(self.spacing)
        A[:3, 3] = np.array(self._origin)
        return A

    @property
    def grid(self) -> Grid:
        """This image's voxel grid as a :class:`Grid` value object.

        Uses only the first 3 shape dims — an ``Image`` read via the generic 4-D-tolerant
        path (e.g. a 4-D NIfTI) still exposes a 3-D spatial grid; :class:`Grid` requires
        exactly 3.
        """
        shape = self.shape
        return Grid.from_components(
            (shape[0], shape[1], shape[2]), self.spacing, self.origin, self.direction
        )

    def same_grid(self, other: Image, *, atol: float = 1e-4) -> bool:
        """Check whether two images share the same voxel grid.

        Grid identity = same shape AND affine (origin, spacing, direction) equal within
        ``atol``. Tolerance — not exact equality — because on-disk formats carry
        different float precision (cf. ITK Coordinate/DirectionTolerance).

        Deliberately NOT delegated to :func:`~clarinet.services.image.grid.grid_relation`:
        that predicate's ``SAME`` verdict allows a half-voxel offset window (meant for
        disk-boundary mirror detection in ``grid_io.assert_same_grid_on_disk``), which
        would silently loosen this method's near-exact pre-overlay guard. Sources its
        affine from :attr:`grid` (single formula authority) but keeps its own tight,
        atol-only comparison on the full affine.
        """
        if self.shape != other.shape:
            return False
        return bool(np.allclose(self.grid.affine, other.grid.affine, atol=atol, rtol=0))

    def _grid_summary(self) -> str:
        """One-line grid description for mismatch diagnostics.

        Thin wrapper — :class:`Grid` (via :attr:`grid`) is the single formatting
        authority; see :meth:`Grid.summary`.
        """
        return self.grid.summary()

    def assert_same_grid(self, other: Image, *, atol: float = 1e-4) -> None:
        """Raise :class:`GeometryMismatchError` if *other* is not on this image's grid.

        Fail-fast guard mirroring ITK's ``VerifyInputInformation``. Use before any
        index-wise overlay of two images/segmentations that must share a grid.
        """
        if self.same_grid(other, atol=atol):
            return
        raise GeometryMismatchError(
            "Images do not occupy the same physical grid:\n"
            f"  self : {self._grid_summary()}\n"
            f"  other: {other._grid_summary()}\n"
            "Resample onto a common grid (reindex_to / conform_seg_to_grid) "
            "or pass resample=True to opt into automatic nearest-neighbour resampling."
        )

    def reindex_to(self, target: Image, *, order: Literal[0, 1] = 1) -> Self:
        """Resample this image into *target*'s voxel grid.

        Args:
            target: Image whose grid (direction, origin, spacing, shape) defines
                the output coordinate system.
            order: Interpolation order (0 = nearest-neighbor, 1 = linear).

        Returns:
            New image with target's spatial metadata and resampled data.
        """
        from scipy.ndimage import affine_transform

        try:
            mapping = np.linalg.inv(self.affine_4x4) @ target.affine_4x4
            resampled = affine_transform(
                self.img,
                mapping[:3, :3],
                offset=mapping[:3, 3],
                output_shape=target.shape,
                order=order,
                mode="constant",
                cval=0.0,
            )
        except Exception as exc:
            raise ImageError(f"Failed to reindex image onto target grid: {exc}") from exc
        result = type(self)(template=target)
        result.img = resampled
        return result

    def read(self, file_path: Path, *, load_data: bool = True, dtype: Any = None) -> None:
        """Read an image file, dispatching by extension.

        Args:
            file_path: Path to a .nii, .nii.gz, or .nrrd file.
            load_data: When False, populate grid metadata + ``shape`` from the header
                and leave voxels unloaded (``has_data`` stays False). The #452 lean path.
            dtype: Force voxels to this numpy dtype on load, reading the on-disk dtype
                and casting once (no float64 intermediate). ``None`` keeps today's
                behavior (NIfTI float64 via ``get_fdata``, NRRD native).

        Raises:
            ImageError: If the file extension is unsupported.
            ImageReadError: If reading the file fails.
        """
        file_path = Path(file_path)
        self._source_path = file_path
        suffixes = file_path.suffixes

        if ".nii" in suffixes:
            self.read_nifti(file_path, load_data=load_data, dtype=dtype)
        elif ".nrrd" in suffixes:
            self.read_nrrd(file_path, load_data=load_data, dtype=dtype)
        else:
            raise ImageError(
                f"Unsupported file extension: {''.join(suffixes)}. "
                "Supported formats: .nii, .nii.gz, .nrrd"
            )

    def read_nifti(self, file_path: Path, *, load_data: bool = True, dtype: Any = None) -> None:
        """Read a NIfTI file (.nii or .nii.gz).

        Args:
            file_path: Path to the NIfTI file.
            load_data: When False, read only the header (grid + shape); leave voxels
                unloaded. ``nibabel.load`` is already lazy — no data block is touched.
            dtype: When set, load voxels via ``np.asarray(dataobj, dtype=dtype)``
                (single cast, no float64 intermediate, no ``get_fdata`` proxy cache).
                ``None`` keeps ``get_fdata()`` (float64).

        Raises:
            ImageReadError: If the file cannot be read.
        """
        file_path = Path(file_path)
        try:
            self._nifti_image = nibabel.loadsave.load(str(file_path))
        except Exception as e:
            raise ImageReadError(f"Failed to read NIfTI file: {file_path}") from e

        self._source_path = file_path
        affine = self._nifti_image.affine
        zooms = nibabel.affines.voxel_sizes(affine)
        self.spacing = tuple(zooms[:3])
        # NIfTI affine is in RAS; convert to internal LPS representation
        direction_ras = affine[:3, :3] / zooms[:3]
        self._direction = _LPS_TO_RAS @ direction_ras
        origin_ras = np.array([affine[0, 3], affine[1, 3], affine[2, 3]])
        origin_lps = _LPS_TO_RAS @ origin_ras
        self._origin = (float(origin_lps[0]), float(origin_lps[1]), float(origin_lps[2]))
        self._shape = tuple(int(s) for s in self._nifti_image.header.get_data_shape())
        self._filetype = FileType.NIFTI

        if not load_data:
            self._img = None
            logger.debug(f"Read NIfTI header {file_path.name}: shape={self._shape} (metadata only)")
            return
        if dtype is None:
            self.img = self._nifti_image.get_fdata()
        else:
            self.img = np.asarray(self._nifti_image.dataobj, dtype=dtype)
        logger.debug(f"Read NIfTI {file_path.name}: shape={self.shape}, dtype={self.img.dtype}")

    def read_nrrd(self, file_path: Path, *, load_data: bool = True, dtype: Any = None) -> None:
        """Read an NRRD file.

        Args:
            file_path: Path to the NRRD file.
            load_data: When False, read only the header (``nrrd.read_header``) for grid
                + shape; leave voxels unloaded.
            dtype: When set, cast native voxels once via ``astype(dtype, copy=False)``.
                pynrrd reads the file's native dtype (never float64); ``None`` keeps it.

        Raises:
            ImageReadError: If the file cannot be read; if the header is not 3-D (use
                :class:`~clarinet.services.image.layered_segmentation.LayeredSegmentation`
                for a 4-D multi-layer ``.seg.nrrd``); or if ``space directions`` is
                present with an unsupported/missing ``space`` field.
        """
        file_path = Path(file_path)
        data: np.ndarray | None = None
        try:
            if load_data:
                data, header = nrrd.read(str(file_path))
            else:
                header = nrrd.read_header(str(file_path))
        except Exception as e:
            raise ImageReadError(f"Failed to read NRRD file: {file_path}") from e

        self._nrrd_header = header
        self._source_path = file_path

        sizes = [int(s) for s in header["sizes"]]
        if len(sizes) != 3:
            raise ImageReadError(
                f"{file_path}: read_nrrd expects a 3-D volume, got a {len(sizes)}-D header "
                f"(sizes={sizes}). A 4-D multi-layer .seg.nrrd is read via "
                "LayeredSegmentation, or grid_io.read_grid for grid-only access."
            )

        # Prefer space directions (carries both spacing and orientation)
        space_dirs = header.get("space directions")
        if space_dirs is not None:
            raw_origin = header.get("space origin")
            arr, origin = _nrrd_space_to_lps(
                header.get("space"),
                np.asarray(space_dirs[:3], dtype=float),
                np.asarray(raw_origin[:3], dtype=float) if raw_origin is not None else None,
            )
            norms = np.linalg.norm(arr, axis=1)
            self.spacing = (float(norms[0]), float(norms[1]), float(norms[2]))
            self._direction = (arr / norms[:, np.newaxis]).T
            if origin is not None:
                self._origin = (float(origin[0]), float(origin[1]), float(origin[2]))
        else:
            spacings = header.get("spacings")
            if spacings is not None:
                self.spacing = tuple(spacings[:3])
            space_origin = header.get("space origin")
            if space_origin is not None:
                vals = space_origin[:3]
                self._origin = (float(vals[0]), float(vals[1]), float(vals[2]))

        self._shape = tuple(sizes)
        self._filetype = FileType.NRRD

        if not load_data:
            self._img = None
            logger.debug(f"Read NRRD header {file_path.name}: shape={self._shape} (metadata only)")
            return
        assert data is not None  # load_data=True guarantees nrrd.read() assigned it
        if dtype is not None:
            data = data.astype(dtype, copy=False)
        self.img = data
        logger.debug(f"Read NRRD {file_path.name}: shape={self.shape}, dtype={self.img.dtype}")

    def read_dicom_series(self, directory: Path) -> None:
        """Read a DICOM series from a directory.

        Args:
            directory: Path to directory containing .dcm files.

        Raises:
            ImageReadError: If reading fails or directory is empty.
        """
        from clarinet.services.image.dicom_volume import read_dicom_series

        directory = Path(directory)
        data, spacing, origin, direction = read_dicom_series(directory)
        self._source_path = directory
        self.spacing = spacing
        self._origin = origin
        self._direction = direction
        self.img = data
        self._filetype = FileType.DICOM
        self._shape = tuple(int(s) for s in data.shape)
        logger.debug(f"Read DICOM series from {directory}: shape={self.shape}")

    def read_slice(
        self, file_path: Path, index: int, *, axis: int = 2, dtype: Any = None
    ) -> np.ndarray:
        """Read a single 2-D slice without materializing the full volume.

        Populates grid metadata + ``shape`` (like ``read(..., load_data=False)``) but
        does not set ``img``. NIfTI is lazy via ``dataobj`` (reads only the slice's
        region; ``.nii.gz`` still sequential-decompresses to ``index`` but returns one
        small array). NRRD has no lazy proxy — full read then index (rare: NRRD segs go
        through ``LayeredSegmentation``).

        Args:
            file_path: NIfTI or NRRD path.
            index: Slice index along ``axis``.
            axis: Axis to slice (default 2 = axial).
            dtype: Cast the returned slice to this dtype.

        Returns:
            The 2-D slice as a numpy array.
        """
        file_path = Path(file_path)
        suffixes = file_path.suffixes
        if ".nii" in suffixes:
            self.read_nifti(file_path, load_data=False)
            slicer: list[Any] = [slice(None)] * len(self.shape)
            slicer[axis] = index
            arr = np.asarray(self._nifti_image.dataobj[tuple(slicer)])
        elif ".nrrd" in suffixes:
            logger.debug(f"read_slice on NRRD {file_path.name}: non-lazy full read then index")
            self.read_nrrd(file_path, load_data=False)
            data, _ = nrrd.read(str(file_path))
            slicer = [slice(None)] * data.ndim
            slicer[axis] = index
            arr = data[tuple(slicer)]
        else:
            raise ImageError(
                f"Unsupported file extension for read_slice: {''.join(suffixes)}. "
                "Supported formats: .nii, .nii.gz, .nrrd"
            )
        if dtype is not None:
            arr = arr.astype(dtype, copy=False)
        return arr

    def unload(self) -> None:
        """Drop voxel data but keep grid metadata and ``shape``.

        Frees the resident array (up to a float64 volume) while leaving the image usable
        for grid checks (``same_grid``, ``affine_4x4``, ``shape``) and the lazy NIfTI
        ``dataobj`` proxy.
        """
        self._img = None
        # The default (dtype=None) NIfTI read aliases self._img to nibabel's internal
        # get_fdata() cache; dropping only our reference leaves the volume resident.
        if self._nifti_image is not None:
            self._nifti_image.uncache()

    def close(self) -> None:
        """Release voxel data **and** the lazy file proxy (mmap).

        Beyond ``unload``, drops ``_nifti_image`` so the on-disk mmap/proxy is released.
        Called by ``__exit__``.
        """
        self._img = None
        self._nifti_image = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def save(self, filename: str, directory: Path | None = None) -> Path:
        """Save the image in its original format.

        Args:
            filename: Base filename (without extension).
            directory: Output directory. Uses source directory if not specified.

        Returns:
            Path to the saved file.

        Raises:
            ImageWriteError: If saving fails.
            ImageError: If the file type is unknown or unsupported.
        """
        if directory is None:
            if self._source_path is None:
                raise ImageError("No source path and no directory specified")
            directory = (
                self._source_path if self._source_path.is_dir() else self._source_path.parent
            )
        directory = Path(directory)

        match self._filetype:
            case FileType.NIFTI:
                output_path = directory / f"{filename}.nii.gz"
                self._save_nifti(output_path)
            case FileType.NRRD:
                output_path = directory / f"{filename}.nrrd"
                self._save_nrrd(output_path)
            case FileType.DICOM:
                raise ImageError(
                    "DICOM writing is not supported. Use save_as() to convert to NIfTI or NRRD."
                )
            case _:
                raise ImageError("Cannot save: unknown file type")

        logger.debug(f"Saved image to {output_path}")
        return output_path

    def save_as(self, path: Path, filetype: FileType) -> Path:
        """Save the image in a specific format at an exact path.

        Args:
            path: Full output file path (including extension).
            filetype: Target format.

        Returns:
            Path to the saved file.

        Raises:
            ImageWriteError: If saving fails.
            ImageError: If the format is not supported for writing.
        """
        path = Path(path)
        match filetype:
            case FileType.NIFTI:
                self._save_nifti(path)
            case FileType.NRRD:
                self._save_nrrd(path)
            case FileType.DICOM:
                raise ImageError(
                    "DICOM writing is not supported. "
                    "Writing DICOM requires UID generation and IOD specification."
                )
            case _:
                raise ImageError(f"Cannot save as {filetype.value}: unsupported format")
        logger.debug(f"Saved image as {filetype.value} to {path}")
        return path

    def _save_nifti(self, path: Path) -> None:
        """Write voxel data to a NIfTI file."""
        try:
            # Convert internal LPS to NIfTI RAS
            direction_ras = _LPS_TO_RAS @ self._direction
            affine = np.eye(4)
            affine[:3, :3] = direction_ras * np.array(self.spacing)
            affine[:3, 3] = _LPS_TO_RAS @ np.array(self._origin)
            new_image = nibabel.Nifti1Image(self.img, affine, dtype=self.img.dtype)
            nibabel.save(new_image, str(path))
        except Exception as e:
            raise ImageWriteError(f"Failed to write NIfTI file: {path}") from e

    def _save_nrrd(self, path: Path) -> None:
        """Write voxel data to an NRRD file."""
        try:
            header: dict[str, Any] = {}
            if self._nrrd_header is not None:
                # Drop all segment keys, then re-add only those reconciled to the
                # labels actually present (prunes stale entries left by set operations
                # and grid-dependent extents). Guarantees a written segmentation never
                # names a label value absent from its voxel data.
                header = {k: v for k, v in self._nrrd_header.items() if not _is_segment_key(k)}
                if any(_is_segment_key(k) for k in self._nrrd_header):
                    header.update(
                        _reconcile_segment_metadata(self._nrrd_header, _present_labels(self.img))
                    )
            # Always write canonical spatial metadata
            space_dirs = (self._direction * np.array(self.spacing)).T
            header["space directions"] = space_dirs
            header["space origin"] = np.array(self._origin)
            header.pop("spacings", None)  # space directions supersedes spacings
            if "space" not in header:
                header["space"] = "left-posterior-superior"
            nrrd.write(str(path), self.img, header)
        except Exception as e:
            raise ImageWriteError(f"Failed to write NRRD file: {path}") from e
