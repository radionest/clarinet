"""RecordType definitions for the project (Python config mode).

All FileDef and RecordDef instances live in this single file. The path is wired
in settings.toml via ``config_record_types_file``.

See `.claude/rules/definitions.md` for the full reference.
"""

from clarinet.flow import FileDef, FileRef, RecordDef

# ---------------------------------------------------------------------------
# File definitions
# ---------------------------------------------------------------------------
# TODO: replace the example below with your project's file definitions.

example_segmentation = FileDef(
    pattern="example_{user_id}.seg.nrrd",
    level="STUDY",
    description="Example: per-doctor segmentation file at study level",
)


# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------
# TODO: replace the examples below with your project's record types.

first_check = RecordDef(
    name="first-check",
    description="Initial assessment of every study in the trial",
    label="First check",
    level="STUDY",
    role="doctor",
    min_records=1,
    max_records=2,
    data_schema="schemas/first-check.schema.json",
)

example_segment = RecordDef(
    name="example-segment",
    description="Example segmentation task — open the study in 3D Slicer",
    label="Example segment",
    level="STUDY",
    role="doctor",
    min_records=1,
    max_records=2,
    slicer_script="scripts/example.py",
    slicer_result_validator="validators/example_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(example_segmentation, "output")],
)
