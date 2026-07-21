---
paths:
  - "plan/definitions/**"
---

# The `plan/definitions/` section

The only place in the project where `FileDef` and `RecordDef` are declared. One file — `record_types.py` — whose path is set in `settings.toml` (`config_record_types_file`).

```python
from clarinet.flow import FileDef, FileRef, RecordDef
```

These three classes fully describe the file types and workflow steps. All behavioral logic (triggers, actions, validation) lives in other sections.

## `FileDef` — file description

```python
master_model = FileDef(
    pattern="master_model.seg.nrrd",
    level="PATIENT",
    description="Master model — one ROI per defect with unique number",
)
```

| Field | Type | Purpose |
|---|---|---|
| `pattern` | `str` | File name or a template with placeholders |
| `level` | `"PATIENT"` / `"STUDY"` / `"SERIES"` | DICOM hierarchy level whose working folder holds the file |
| `multiple` | `bool` | `True` — a glob collection, `False` — a single file |
| `description` | `str` | Documentation for the agent and the UI |
| `name` | `str` | Auto-generated from the variable name (`master_model`); can be set explicitly |

### Placeholders in `pattern`

| Placeholder | Value |
|---|---|
| `{patient_id}`, `{study_uid}`, `{series_uid}` | Identifiers from the DICOM hierarchy (anonymized) |
| `{user_id}` | ID of the user who created the record (for "per-inspector" files) |
| `{origin_type}` | `record.record_type_name` — lets you name files after the originating record type |
| `{parent_id}` | `record.parent_record_id` (FK value) — the exact per-parent discriminator, resolved without loading the parent |
| `{data.FIELD}` | A field from `record.data` |

Pattern-resolution details are in `<clarinet>/clarinet/.claude/rules/file-registry.md`.

### `level` semantics

- **PATIENT** — the file lives in `<storage>/<patient_id>/`. Available to any of the patient's records (master models, shared references).
- **STUDY** — the file lives in `<storage>/<patient_id>/<study_uid>/`. Used for study-level artifacts (single-study segmentations).
- **SERIES** — the file lives in `<storage>/<patient_id>/<study_uid>/<series_uid>/`. Used for series-level artifacts (NIfTI volumes, projections).

A file's level must be **no deeper** than the level of the record referencing it. A PATIENT file is available to everyone; a SERIES file is available only to SERIES records.

## `FileRef` — binding a file to a RecordDef

```python
FileRef(segmentation, "output")     # positional
FileRef(segmentation, role="input") # named
```

| Field | Type | Purpose |
|---|---|---|
| `file` | `FileDef` | Reference to a `FileDef` object declared earlier in the same file |
| `role` | `"input"` / `"output"` / `"intermediate"` | Semantics: input (required before execution) / output (created) / intermediate |
| `required` | `bool` (default `True`) | Whether the file must exist by the time the record is finalized |
| `allow_path_collision` | `bool` (default `False`) | Opts this one binding out of output-path uniqueness validation (the author guarantees uniqueness, e.g. via `{data.FIELD}`) |

`output_file` in a Slicer script is the path to the **first** `FileRef` with `role="output"` from `RecordDef.files`.

## `RecordDef` — record-type description

```python
segment_ct = RecordDef(
    name="segment-ct-single",
    description="CT defect segmentation — single study only",
    label="CT segment (single)",
    level="STUDY",
    role="inspector_CT",
    min_records=2,
    max_records=4,
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    data_schema="schemas/segment-ct.schema.json",  # optional
)
```

### Required fields

| Field | Description |
|---|---|
| `name` | kebab-case, 5-30 characters. The identifier used in the DSL and the URL. |
| `level` | `"PATIENT"` / `"STUDY"` / `"SERIES"` |

### Optional fields

| Field | Description |
|---|---|
| `description` | Detailed description for the agent and the UI |
| `label` | Short display name for the UI |
| `role` (alias `role_name`) | Who performs it: `"doctor"`, `"auto"`, `"expert"`, or a custom role from `extra_roles` |
| `min_records`, `max_records` | How many records of this type must/may exist per level context (patient/study/series) |
| `unique_by` | Uniqueness partitions within the level context: set from `{"user", "parent"}` (default both), or `None` — no uniqueness. Legacy `unique_per_user=` is accepted, translated and deprecation-warned |
| `files` | `list[FileRef(...)]` — link to `FileDef` |
| `data_schema` | A `"schemas/X.schema.json"` path or an inline `dict`. The path is relative to `config_tasks_path` (i.e. `plan/`). Shared sub-schemas can be extracted into a separate file and pulled into any schema via `$ref` — see the `schemas` section |
| `slicer_script` | Path to the Slicer script: `"scripts/segment.py"` |
| `slicer_result_validator` | Path to the validator: `"validators/segment_validator.py"` |
| `slicer_context_hydrators` | `list[str]` — names of hydrator functions that inject variables into Slicer |
| `slicer_script_args` | `dict[str, Any]` — static constants available in the Slicer script |

## Links between sections

`RecordDef` references files in other project sections by convention:

| Field | What it points to |
|---|---|
| `slicer_script="scripts/X.py"` | A file in `plan/scripts/` |
| `slicer_result_validator="validators/X.py"` | A file in `plan/validators/` |
| `slicer_context_hydrators=["name"]` | The `@slicer_context_hydrator("name")` decorator in `plan/slicer_hydrators.py` |
| `data_schema="schemas/X.schema.json"` | A file in `plan/schemas/` |
| `files=[FileRef(file_def, ...)]` | A `FileDef` declared earlier in `record_types.py` |
| `role="custom_role"` | Must be present in `extra_roles` in `settings.toml` |

All paths are relative to `config_tasks_path` (`plan/`), not to the current `record_types.py` file itself.

## Common mistakes

- **A custom role not in `settings.toml`**. `RecordDef(role="technician")` without `extra_roles = [..., "technician"]` will fail during config loading.
- **A schema path relative to `definitions/`**. `data_schema="../schemas/X.schema.json"` is wrong — the path is relative to `plan/`, not `plan/definitions/`. Correct: `"schemas/X.schema.json"`.
- **Slicer fields without files**. You set `slicer_script="scripts/foo.py"` but there's no such file — the config loads fine, but the task run will break.
- **A file's `level` deeper than the record's level**. You can't set a SERIES-level file as input to a STUDY record (it doesn't know which series to read).
- **Hydrator name doesn't match the decorator**. `RecordDef` specifies `slicer_context_hydrators=["best_series"]`, but the code has `@slicer_context_hydrator("best_series_from_first_check")`. The names must match exactly.

## Full annotated example

```python
from clarinet.flow import FileDef, FileRef, RecordDef

# --- File definitions ---

segmentation = FileDef(
    pattern="segmentation_{user_id}.seg.nrrd",  # name depends on the user
    level="STUDY",                                # lives in the study folder
    description="Inspector defect segmentation",
)

# --- Record types ---

first_check = RecordDef(
    name="first-check",                            # kebab-case
    description="Initial study assessment",
    label="First check",
    level="STUDY",
    role="doctor",                                 # standard role
    min_records=2,                                 # each study is reviewed by 2 doctors
    max_records=2,
    data_schema="schemas/first-check.schema.json", # relative to plan/
)

segment_ct = RecordDef(
    name="segment-ct-single",
    label="CT segment",
    level="STUDY",
    role="inspector_CT",                           # must be in extra_roles
    min_records=2,
    max_records=4,
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],       # output_file = path to this file
)
```
