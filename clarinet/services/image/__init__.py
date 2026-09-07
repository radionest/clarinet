"""Image processing service — image I/O, segmentation, and format conversion."""

from clarinet.services.image.coco2nii import COCODataset, coco_to_segmentation
from clarinet.services.image.grid import Grid, GridRelation, RelationKind, grid_relation
from clarinet.services.image.grid_io import (
    PairVerdict,
    assert_same_grid_on_disk,
    classify_pair,
    read_grid,
)
from clarinet.services.image.image import FileType, Image
from clarinet.services.image.layered_segmentation import LayeredSegmentation
from clarinet.services.image.nrrd_repair import declare_nrrd_space
from clarinet.services.image.nrrd_space import NrrdGrid, nrrd_grid_from_header
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
    "NrrdGrid",
    "OrientationUnverifiable",
    "PairVerdict",
    "PropName",
    "RelationKind",
    "Segmentation",
    "assert_same_grid_on_disk",
    "classify_pair",
    "coco_to_segmentation",
    "conform_seg_to_grid",
    "declare_nrrd_space",
    "grid_relation",
    "is_volume_misoriented",
    "nrrd_grid_from_header",
    "read_grid",
]
