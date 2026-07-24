"""Image processing service — image I/O, segmentation, and format conversion."""

from clarinet.services.image.coco2nii import COCODataset, coco_to_segmentation
from clarinet.services.image.grid import Grid, GridRelation, RelationKind, grid_relation
from clarinet.services.image.grid_io import assert_same_grid_on_disk, read_grid
from clarinet.services.image.image import FileType, Image
from clarinet.services.image.layered_segmentation import LayeredSegmentation
from clarinet.services.image.orientation import OrientationUnverifiable, is_volume_misoriented
from clarinet.services.image.segmentation import (
    PropName,
    Segmentation,
    conform_seg_to_grid,
)

__all__ = [
    "COCODataset",
    "FileType",
    "Grid",
    "GridRelation",
    "Image",
    "LayeredSegmentation",
    "OrientationUnverifiable",
    "PropName",
    "RelationKind",
    "Segmentation",
    "assert_same_grid_on_disk",
    "coco_to_segmentation",
    "conform_seg_to_grid",
    "grid_relation",
    "is_volume_misoriented",
    "read_grid",
]
