---
paths:
  - "plan/slicer_hydrators.py"
  - "plan/scripts/**"
  - "plan/validators/**"
---

# Slicer integration: hydrators / scripts / validators

These three sections are grouped together because they're tightly linked via **inject vars** — variables the framework passes into the environment of a Slicer script and its validator. A hydrator computes a variable; the script uses it; the validator sees it too.

```
slicer_hydrators.py  →  compute inject vars (plan root)
scripts/    →  *.py (run inside 3D Slicer)      # use inject vars
validators/ →  *_validator.py                   # run after the script, see the same vars
```

---

## Part A — Hydrators (`plan/slicer_hydrators.py`)

Async functions that query the DB before a Slicer script runs and return a dict of variables to inject into Slicer. A single file — `slicer_hydrators.py` (the `config_context_hydrators_file` default), living at the root of `plan/`.

### Decorator and signature

```python
from typing import Any

from clarinet.models.record import RecordRead
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.slicer.context_hydration import (
    SlicerHydrationContext,
    slicer_context_hydrator,
)


@slicer_context_hydrator("best_series_from_first_check")
async def hydrate_best_series_from_first_check(
    record: RecordRead,
    _context: dict[str, Any],
    ctx: SlicerHydrationContext,
) -> dict[str, Any]:
    """Inject best_series_uid from the first-check record."""
    criteria = RecordSearchCriteria(
        record_type_name="first-check",
        study_uid=record.study_uid,
    )
    first_checks = await ctx.record_repo.find_by_criteria(criteria)
    if not first_checks:
        return {}

    best_series = (first_checks[0].data or {}).get("best_series")
    if not best_series:
        return {}

    return {"best_series_uid": best_series}
```

| Parameter | What's inside |
|---|---|
| `record` | `RecordRead` — the record the context is being assembled for |
| `_context` | dict accumulated by previous hydrators + auto-injected variables. Usually not needed (hence the `_` prefix); used only in rare cases where one hydrator depends on another's result (e.g. for `working_folder`) |
| `ctx` | `SlicerHydrationContext` with access to repositories |

The name in the decorator (`"best_series_from_first_check"`) is what you put in `RecordDef.slicer_context_hydrators=[...]`. It must match character-for-character.

### `SlicerHydrationContext`

```python
ctx.study_repo.find_by_patient(patient_id)        # all of a patient's studies
ctx.study_repo.get_with_series(study_uid)         # study + loaded series
ctx.record_repo.get(record_id)                    # a single record
ctx.record_repo.find_by_criteria(criteria)        # complex search
```

`RecordSearchCriteria` supports filters by `record_type_name`, `patient_id`, `study_uid`, `series_uid`, status, etc. Full list — `clarinet/repositories/record_repository.py`.

### Return value

`dict[str, Any]`. The keys become **variable names** in the Slicer script and validator. If there isn't enough data, return `{}` (don't raise) — the framework simply won't add those variables, and the script can check for them via `if best_series_uid is not None`.

### Hydrator vs `slicer_script_args`

- **Hydrator** — a dynamic value that requires a DB query (the best series' UID, another patient's file path).
- **`slicer_script_args`** — static constants known at the time `RecordDef` is described (segment colors, editor mode, brush size).

---

## Part B — Slicer scripts (`plan/scripts/`)

Bare Python scripts that run inside the 3D Slicer environment. Each file is one task type.

### Inject vars: what's available in the script

**Always auto-injected by the framework**:

| Variable | When |
|---|---|
| `working_folder` | `str` — absolute path to the record's working folder (PATIENT/STUDY/SERIES) |
| `output_file` | `str` — path to the **first** `FileRef` with `role="output"` from `RecordDef.files` |
| `study_uid` | `str` — DICOM Study UID (STUDY/SERIES level only) |
| `series_uid` | `str` — DICOM Series UID (SERIES level only) |
| `pacs_host`, `pacs_port`, `pacs_aet`, `pacs_login`, `pacs_password` | if PACS is configured in settings |

**Hydrator-injected**: whatever the functions in `RecordDef.slicer_context_hydrators` returned.

**User constants**: whatever is specified in `RecordDef.slicer_script_args`.

### Mandatory docstring

At the top of every script — a docstring listing the context vars. This is a **contract** between the script and the framework: it makes it easier for the agent to orient itself, ensures the validator gets the same vars, and makes the correspondence with the `RecordDef` explicit.

```python
"""Slicer script — lesion segmentation on a single study.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    study_uid: DICOM Study UID (auto, STUDY-level).
    output_file: Path to the first OUTPUT file definition (auto).
    best_series_uid: From hydrator best_series_from_first_check (may be None).
    pacs_*: PACS connection parameters (auto).
"""
```

### `SlicerHelper`

The main helper for PACS / segmentation / layout / alignment. Full API + VTK pitfalls — `<clarinet>/clarinet/.claude/rules/slicer-helper-api.md`. Basic toolkit:

```python
s = SlicerHelper(working_folder)

# Load from PACS (window = CT soft tissue)
s.load_study_from_pacs(study_uid, window=(-200, 300))
s.load_series_from_pacs(study_uid, series_uid, window=(-200, 300))

# Segmentation
seg = (
    s.create_segmentation("Segmentation")
    .add_segment("mts", (1.0, 0.0, 0.0))     # red
    .add_segment("benign", (0.0, 1.0, 0.0))  # green
)
seg = s.load_segmentation(output_file, "Segmentation")
s.copy_segments(src_seg, dst_seg, empty=True)
s.sync_segments(src_seg, dst_seg, empty=True)

# UI
s.setup_editor(seg, effect="Paint", brush_size=5.0)
s.set_layout("axial")
s.set_dual_layout(vol_a, vol_b, seg_a=..., seg_b=..., linked=False)
s.annotate("Segment all lesions")
s.add_view_shortcuts()

# Alignment
align_tf = s.align_by_center(target, model, moving_segmentation=projection)
s.refine_alignment_by_centroids(projection, master_seg, align_tf)
```

### Idempotency

A script may be reopened (e.g. a doctor wants to continue a segmentation). So the standard pattern is checking whether the output already exists:

```python
import os

if os.path.isfile(output_file):
    seg = s.load_segmentation(output_file, "Segmentation")
else:
    seg = s.create_segmentation("Segmentation").add_segment("mts", (1.0, 0.0, 0.0))
```

### Common patterns

**Single-volume segmentation**: load the study (or a single series), create/load a segmentation, configure the editor, done.

**Dual-volume comparison** (comparing the current study against a reference): `set_dual_layout(linked=False)` for independent navigation in two viewports. Left — the model, right — the target series + projection.

### Lint comments

Since the variables are injected into the global namespace, mypy/ruff don't know they exist. Add this on every line that uses them:

```python
s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821
```

`name-defined` silences mypy, `F821` silences pyflakes (undefined name).

---

## Part C — Validators (`plan/validators/`)

Bare Python scripts that run in Slicer **after** the user clicks "save". The same globals as in the script are available: `slicer`, hydrator vars, `output_file`. Plus the built-in `export_segmentation` helper.

### Basic pattern

```python
"""Validator — check segment names and export the Segmentation node."""

node = slicer.util.getNode("Segmentation")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

expected = {"mts", "unclear", "benign"}
current = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    current.add(seg.GetSegment(sid).GetName())

if current != expected:
    raise ValueError(f"Expected segments {expected}, got {current}")

export_segmentation("Segmentation", output_file)  # type: ignore[name-defined]  # noqa: F821
```

Structure:

1. `node = slicer.util.getNode("Name")` — get the MRML node.
2. Validation: segment names, types, consistency with the previous state.
3. `raise ValueError(...)` on a problem — the user will see the error and won't be able to finalize the record.
4. `export_segmentation("Name", output_file)` — write the segmentation to `.seg.nrrd` via the built-in helper.

### Common checks

**Required segment set** — all needed segments are present:
```python
expected = {"mts", "unclear", "benign"}
if current != expected:
    raise ValueError(f"Expected {expected}, got {current}")
```

**Auto-numbering** — for master models with numeric segment names: fill in missing numbers into empty segments, verify uniqueness.

**Immutability** — if the file already exists, verify that no existing segment has disappeared or been renamed (protects against accidentally destroying historical data):
```python
import os, nrrd
if os.path.isfile(output_file):
    _, header = nrrd.read(output_file)
    prev_names = {header[f"Segment{i}_Name"] for i in range(...)}
    missing = prev_names - set(current_names)
    if missing:
        raise ValueError(f"Cannot remove segments: {missing}")
```

### Naming

`{task_name}_validator.py` (e.g. `segment_validator.py` for the `segment.py` script). Linked via `RecordDef(slicer_result_validator="validators/segment_validator.py")`.
