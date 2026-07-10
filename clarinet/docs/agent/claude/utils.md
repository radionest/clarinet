---
paths:
  - "plan/utils/**"
---

# The `plan/utils/` section

Project-specific helper modules used by pipeline tasks, validators, and scripts. This section isn't managed directly by the framework — its contents and structure are entirely up to you, but the conventions below help keep it clean.

## What belongs here

- **Shared constants**: segment label maps, category names, classification thresholds.
  ```python
  SEG_LABELS: dict[str, int] = {"mts": 1, "unclear": 2, "benign": 3}
  ```
- **File I/O wrappers**: reading/writing `.seg.nrrd` with segment metadata, reading DICOM metadata, parsing reports.
- **Image processing** not tied to one specific task: label converters, connected components, segmentation metrics (Dice, Hausdorff), morphological operations.
- **Pure helper functions** shared across several pipeline tasks or validators.

## What does NOT belong here

- **API calls to clarinet**. That's the pipeline task's job via `ctx.client` — not a helper's. A helper shouldn't know about the DB or HTTP.
- **Slicer-specific logic** (`slicer.util.getNode`, MRML node manipulation). That belongs in `plan/scripts/` or `plan/validators/`. A helper in `utils` can be imported by any module, including Slicer scripts, but once it starts touching `slicer` it's no longer a helper — it's part of a Slicer task.
- **Workflow business logic** (creating records, status transitions). That belongs in `plan/workflows/pipeline_flow.py`.

## Naming and structure

Topical snake_case files. No hard requirements:

```
plan/utils/
├── __init__.py        # usually empty
├── seg_utils.py       # read/write .seg.nrrd
├── constants.py       # label maps, thresholds
├── image_io.py        # NIfTI/DICOM helpers
└── metrics.py         # Dice, IoU, Hausdorff
```

## Imports from other sections

`plan/` is available as the `clarinet_plan` package (single root — `config_tasks_path`); all imports go through this prefix, and the framework doesn't touch `sys.path`:

```python
# In pipeline_flow.py (workflows/ — recordflow_paths)
from clarinet_plan.utils.seg_utils import save_seg_nrrd, master_label_converter
from clarinet_plan.definitions.record_types import master_model
# relative imports are fine within the same subpackage: from ..utils.seg_utils import ...
```

**Slicer scripts** (`plan/scripts/`, and `slicer_result_validator` text) run **inside the 3D Slicer process**, where the `clarinet_plan` package does NOT exist — they must be self-contained (inline any needed helper code, don't import from `plan/utils/`). Python record-data validators (`plan/validators.py`, loaded by the framework) can use `from clarinet_plan.utils... import ...`.

`plan/utils/__init__.py` can be empty — having the file makes the structure explicit and survives refactors.

---

## The `.seg.nrrd` format (an important special case)

3D Slicer stores segmentations in NRRD format with extra header fields — segment names and label values. If your project works with segmentations, `utils/` usually has read/write wrappers.

### Required header fields

```python
header = {
    "type": "unsigned char",
    "dimension": 3,
    "space": "left-posterior-superior",                    # LPS — Slicer convention
    "space directions": (direction * np.array(spacing)).T, # 3x3 cosine matrix
    "space origin": np.array(origin),                       # XYZ origin
}
```

### Segment metadata

For each segment (i = 0, 1, ...):

```python
header[f"Segment{i}_ID"] = f"Segment_{i}"
header[f"Segment{i}_Name"] = name           # name shown in the UI
header[f"Segment{i}_LabelValue"] = str(lbl) # integer label in the array
header[f"Segment{i}_Layer"] = "0"           # usually "0"
```

### Label converter

A `(segment_name: str) -> int` function that maps a name to an integer label value. The simplest case is numeric names (`"1"` → `1`):

```python
def master_label_converter(name: str) -> int:
    return int(name)
```

For categories (`"mts"` → `1`):

```python
SEG_LABELS = {"mts": 1, "unclear": 2, "benign": 3}
def category_converter(name: str) -> int:
    return SEG_LABELS[name]
```

### Minimal read/write

```python
import nrrd
import numpy as np

def save_seg_nrrd(
    data: np.ndarray,
    path: str,
    segment_names: list[str],
    label_converter,
    *,
    spacing: tuple[float, ...],
    origin: tuple[float, ...],
    direction: np.ndarray,
) -> None:
    header = {
        "type": "unsigned char",
        "dimension": 3,
        "space": "left-posterior-superior",
        "space directions": (direction * np.array(spacing)).T,
        "space origin": np.array(origin),
    }
    for i, name in enumerate(segment_names):
        header[f"Segment{i}_ID"] = f"Segment_{i}"
        header[f"Segment{i}_Name"] = name
        header[f"Segment{i}_LabelValue"] = str(label_converter(name))
        header[f"Segment{i}_Layer"] = "0"
    nrrd.write(path, data.astype(np.uint8), header)


def read_seg_nrrd_labels(path: str) -> dict[int, str]:
    """Returns {label_value: segment_name}."""
    _, header = nrrd.read(path)
    labels: dict[int, str] = {}
    i = 0
    while f"Segment{i}_Name" in header:
        labels[int(header[f"Segment{i}_LabelValue"])] = header[f"Segment{i}_Name"]
        i += 1
    return labels
```

### Alternative: `clarinet.services.image.Segmentation`

The framework provides a numpy/nrrd wrapper — it's often simpler to use than rolling your own:

```python
from clarinet.services.image import Segmentation

seg = Segmentation(autolabel=False)
seg.read(path)
seg.img         # 3D numpy array
seg.spacing     # voxel spacing
seg._origin
seg._direction
seg.count       # voxel count
seg.difference(other_seg, max_overlap_ratio=0.05)
```

Use `Segmentation` for reading and basic operations; `seg_utils.save_seg_nrrd` for writing with custom names and label converters, when `Segmentation.write()` doesn't fit.
