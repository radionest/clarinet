"""COCO JSON segmentation format to NIfTI converter."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from pydantic import BaseModel
from skimage import draw

from clarinet.exceptions.domain import ImageReadError
from clarinet.services.image.image import Image
from clarinet.services.image.segmentation import Segmentation
from clarinet.utils.logger import logger


class COCOInfo(BaseModel):
    """COCO dataset info metadata."""

    mode: str
    studyInstanceUID: str
    dateTime: str


class COCOCategory(BaseModel):
    """COCO annotation category."""

    id: int
    name: str
    superCategory: int | None = None
    description: str


class COCOImage(BaseModel):
    """COCO image entry (one per DICOM slice)."""

    id: int
    width: int
    height: int
    numberOfFrames: int
    seriesInstanceUID: str
    sopInstanceUID: str


class COCOAnnotation(BaseModel):
    """COCO polygon annotation for a single slice."""

    id: int
    imageId: int
    categoryId: int
    area: float
    bbox: tuple[int, int, int, int]
    frameNumber: int
    segmentation: list[list[list[float]]]


class COCODataset(BaseModel):
    """Complete COCO segmentation dataset."""

    info: COCOInfo
    categories: list[COCOCategory]
    images: list[COCOImage]
    annotations: list[COCOAnnotation]

    @property
    def study_uid(self) -> str:
        """Study Instance UID from info."""
        return self.info.studyInstanceUID

    @property
    def series_uid(self) -> str:
        """Series Instance UID from the first image entry."""
        return self.images[0].seriesInstanceUID


def coco_to_segmentation(
    coco_json_path: Path,
    volume: Image,
    separate_labels: bool = True,
) -> Segmentation:
    """Convert a COCO JSON annotation file into a 3D Segmentation.

    Reads polygon annotations from the COCO JSON, rasterizes each polygon
    into a 2D binary mask, and stacks them into the volume's coordinate space.

    Args:
        coco_json_path: Path to the COCO JSON file.
        volume: Reference Image providing shape, spacing, and affine metadata.
        separate_labels: If True, label connected components after stacking.

    Returns:
        Segmentation with rasterized masks in the volume's coordinate space.

    Raises:
        ImageReadError: If the JSON file cannot be read or parsed.
    """
    coco_json_path = Path(coco_json_path)
    try:
        with open(coco_json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ImageReadError(f"Failed to read COCO JSON: {coco_json_path}") from e

    dataset = COCODataset(**data)

    # Build lookup: image_id → COCOImage
    images_by_id = {img.id: img for img in dataset.images}

    output = Segmentation(autolabel=separate_labels, template=volume)

    for annotation in dataset.annotations:
        image_meta = images_by_id.get(annotation.imageId)
        if image_meta is None:
            logger.warning(
                f"Annotation {annotation.id} references unknown imageId {annotation.imageId}"
            )
            continue

        mask = _create_mask(annotation, image_meta)
        # frameNumber is 0-based slice index; imageId-1 as fallback
        slice_idx = annotation.frameNumber
        output.img[mask > 0, slice_idx] = 1

    # Flip along Y axis to match NIfTI orientation convention
    flipped = output.img[:, ::-1, :]
    output.img = flipped

    logger.debug(
        f"Converted COCO {coco_json_path.name}: "
        f"{len(dataset.annotations)} annotations → shape={output.shape}"
    )
    return output


def _create_mask(annotation: COCOAnnotation, image_meta: COCOImage) -> np.ndarray:
    """Rasterize a COCO polygon annotation into a 2D binary mask.

    Args:
        annotation: The COCO annotation with polygon segmentation data.
        image_meta: The COCO image entry with width/height.

    Returns:
        2D binary numpy array of shape (height, width).
    """
    raw_polygon = np.array(annotation.segmentation[0])  # (N, 2), cols=[x, y]
    polygon_rc = raw_polygon[:, ::-1]  # swap to [row, col]
    image_shape = (image_meta.height, image_meta.width)  # (rows, cols)
    mask: np.ndarray = draw.polygon2mask(image_shape, polygon_rc)
    return mask
