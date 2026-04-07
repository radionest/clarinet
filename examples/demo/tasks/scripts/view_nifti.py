"""Slicer script — load and view a pre-converted NIfTI volume.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    study_uid: Study anonymized UID (auto, SERIES-level).
    series_uid: Series anonymized UID (auto, SERIES-level).
    volume_nifti: Absolute path to the NIfTI volume file (auto, from file_registry).
"""

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821

s.load_volume(volume_nifti)  # type: ignore[name-defined]  # noqa: F821
s.set_layout("axial")
s.annotate("NIfTI volume")
