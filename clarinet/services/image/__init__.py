"""Image processing service — medical image I/O, segmentation, and format conversion."""

from clarinet.services.image.coco2nii import COCODataset, coco_to_segmentation
from clarinet.services.image.image import FileType, Image
from clarinet.services.image.segmentation import PropName, Segmentation

__all__ = [
    "COCODataset",
    "FileType",
    "Image",
    "PropName",
    "Segmentation",
    "coco_to_segmentation",
]
