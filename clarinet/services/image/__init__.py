"""Image processing service — medical image I/O, segmentation, and format conversion."""

from clarinet.services.image.coco2nii import COCODataset, coco_to_segmentation
from clarinet.services.image.image import FileType, Image
from clarinet.services.image.layered_segmentation import LayeredSegmentation
from clarinet.services.image.segmentation import (
    PropName,
    Segmentation,
    conform_seg_to_grid,
)

__all__ = [
    "COCODataset",
    "FileType",
    "Image",
    "LayeredSegmentation",
    "PropName",
    "Segmentation",
    "coco_to_segmentation",
    "conform_seg_to_grid",
]
