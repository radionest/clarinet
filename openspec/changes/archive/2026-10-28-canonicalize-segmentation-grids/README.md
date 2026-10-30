# canonicalize-segmentation-grids

Framework-level fix for Z-mirrored segmentation grids: 4-D-safe grid-inspection primitives
(`read_grid`, `grid_relation`, `assert_same_grid_on_disk`), a conform-on-export Slicer path
(non-breaking, Release A), and a canonical right-handed DICOM→NIfTI converter so no ITK runtime
mirrors a fresh segmentation at load (breaking, Release B). No project-specific logic.
