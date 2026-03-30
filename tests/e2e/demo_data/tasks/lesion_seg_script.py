# ruff: noqa: F821
# Slicer DSL script — variables are injected by SlicerHelper at runtime
s = SlicerHelper(working_folder)
s.load_study_from_pacs(study_uid)
seg = s.create_segmentation("lesions")
seg.add_segment("lesions", color=(1.0, 0.0, 0.0))
s.setup_editor(seg, effect="Paint", brush_size=20)
s.set_layout("axial")
