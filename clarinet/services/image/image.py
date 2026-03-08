"""Image class for reading/writing medical images (NIfTI, NRRD, DICOM)."""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any, Self

import nibabel
import nibabel.affines
import nibabel.loadsave
import nrrd
import numpy as np

from clarinet.exceptions.domain import ImageError, ImageReadError, ImageWriteError
from clarinet.utils.logger import logger


class FileType(enum.Enum):
    """Supported medical image file formats."""

    NIFTI = "nifti"
    NRRD = "nrrd"
    DICOM = "dicom"


class Image:
    """3D medical image with format-aware I/O.

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
        self._nrrd_header: dict | None = None
        self.force_dtype: Any = dtype

        if template is not None:
            self._source_path = template._source_path
            self.spacing = template.spacing
            self._origin = template._origin
            self._direction = template._direction.copy()
            self._nifti_image = getattr(template, "_nifti_image", None)
            self._nrrd_header = getattr(template, "_nrrd_header", None)
            self._filetype = template._filetype
            self.img = np.copy(template.img) if copy_data else np.zeros(template.img.shape)

    @property
    def img(self) -> np.ndarray:
        """Voxel data as a numpy array."""
        if self._img is None:
            raise ImageError("Image data is not loaded")
        return self._img

    @img.setter
    def img(self, vol: np.ndarray) -> None:
        if self.force_dtype is not None:
            self._img = vol.astype(self.force_dtype)
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
        """Shape of the voxel array."""
        return self.img.shape

    def read(self, file_path: Path) -> None:
        """Read an image file, dispatching by extension.

        Args:
            file_path: Path to a .nii, .nii.gz, or .nrrd file.

        Raises:
            ImageError: If the file extension is unsupported.
            ImageReadError: If reading the file fails.
        """
        file_path = Path(file_path)
        self._source_path = file_path
        suffixes = file_path.suffixes

        if ".nii" in suffixes:
            self.read_nifti(file_path)
        elif ".nrrd" in suffixes:
            self.read_nrrd(file_path)
        else:
            raise ImageError(
                f"Unsupported file extension: {''.join(suffixes)}. "
                "Supported formats: .nii, .nii.gz, .nrrd"
            )

    def read_nifti(self, file_path: Path) -> None:
        """Read a NIfTI file (.nii or .nii.gz).

        Args:
            file_path: Path to the NIfTI file.

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
        # Normalize columns to get unit direction vectors
        self._direction = affine[:3, :3] / zooms[:3]
        self._origin = (float(affine[0, 3]), float(affine[1, 3]), float(affine[2, 3]))
        self.img = self._nifti_image.get_fdata()
        self._filetype = FileType.NIFTI
        logger.debug(f"Read NIfTI {file_path.name}: shape={self.shape}, dtype={self.img.dtype}")

    def read_nrrd(self, file_path: Path) -> None:
        """Read an NRRD file.

        Args:
            file_path: Path to the NRRD file.

        Raises:
            ImageReadError: If the file cannot be read.
        """
        file_path = Path(file_path)
        try:
            data, header = nrrd.read(str(file_path))
        except Exception as e:
            raise ImageReadError(f"Failed to read NRRD file: {file_path}") from e

        self._nrrd_header = header
        self._source_path = file_path

        # Prefer space directions (carries both spacing and orientation)
        space_dirs = header.get("space directions")
        if space_dirs is not None:
            arr = np.asarray(space_dirs[:3], dtype=float)
            norms = np.linalg.norm(arr, axis=1)
            self.spacing = (float(norms[0]), float(norms[1]), float(norms[2]))
            self._direction = (arr / norms[:, np.newaxis]).T
        else:
            spacings = header.get("spacings")
            if spacings is not None:
                self.spacing = tuple(spacings[:3])

        space_origin = header.get("space origin")
        if space_origin is not None:
            vals = space_origin[:3]
            self._origin = (float(vals[0]), float(vals[1]), float(vals[2]))

        self.img = data
        self._filetype = FileType.NRRD
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
        logger.debug(f"Read DICOM series from {directory}: shape={self.shape}")

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
            affine = np.eye(4)
            affine[:3, :3] = self._direction * np.array(self.spacing)
            affine[:3, 3] = self._origin
            new_image = nibabel.Nifti1Image(self.img, affine, dtype=self.img.dtype)
            nibabel.save(new_image, str(path))
        except Exception as e:
            raise ImageWriteError(f"Failed to write NIfTI file: {path}") from e

    def _save_nrrd(self, path: Path) -> None:
        """Write voxel data to an NRRD file."""
        try:
            header: dict[str, Any] = {}
            if self._nrrd_header is not None:
                header = {k: v for k, v in self._nrrd_header.items() if not k.startswith("Segment")}
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
