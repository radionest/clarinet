# Migration: DICOM slice-axis orientation fix (clarinet 0.10.17)

clarinet 0.10.17 fixes a DICOM→NIfTI reader bug (#453) where SimpleITK/GDCM could
return a slice-axis sign inconsistent with the actual file order on long axial
series, producing an **anatomically flipped** `volume.nii.gz`. Segmentations painted
on a flipped volume are frozen on that grid, so after re-conversion old and new masks
sit on index-reversed grids and any index-wise overlay reads as zero overlap.

The reader fix is **byte-identical for correctly-read series** — only affected series
change on re-conversion. This guide detects and remediates affected series per project.

## 1. Upgrade

Re-pin the project to `clarinet >= 0.10.17` and reinstall (API host and every worker).

## 2. Detect affected series

For each finished conversion record, C-MOVE the series to a temp dir and call the
framework detection primitive:

```python
from clarinet.services.image import is_volume_misoriented, OrientationUnverifiable

try:
    flipped = is_volume_misoriented(volume_nifti_path, dicom_temp_dir)
except OrientationUnverifiable as exc:
    # ground truth could not be established (non-axial / unreadable) — review by hand
    log.warning(f"cannot verify {record.id}: {exc}")
    flipped = None
```

`is_volume_misoriented` is idempotent — an already-remediated volume returns `False`,
so the scan is safe to re-run. It raises `OrientationUnverifiable` (never silently
"correct") when it cannot read ground truth or the series is not dominantly axial.

## 3. Remediate each affected series

For every hit: delete that series' derived artifacts (the `volume.nii.gz`, any masks,
ROI files) and hard-invalidate the conversion record so the project's cascade
regenerates them through the now-correct reader.

- The derived-artifact list is **project-specific** — each project owns its file set
  and its conversion-record invalidation target.
- Run **dry-run by default**; require an explicit `--apply` flag to write.
- The operation is idempotent (a remediated volume is no longer detected in step 2).

Reference template (adapt the artifact list + invalidation target per project):
`clarinet_lymphoma_muscles/scripts/remediate_orientation_bug.py`.

## 4. Already-frozen segmentations on a still-correct volume

If a volume was correct but a mask drifted onto a divergent earlier-epoch grid, use
`clarinet.services.image.conform_seg_to_grid(seg_path, grid_path)` to re-align the
mask to the canonical series volume without re-conversion. This is also the remedy the
Slicer set-op guard (#415) now points at when it refuses a mismatched input.

## 5. Audit Slicer set-op call sites (#415)

As part of the same fix, `clarinet/services/slicer/helper.py`'s
`subtract_segmentations` / `binarize_and_split_islands` / `merge_as_pool` now raise
`SlicerHelperError` by default when a non-empty input segmentation's reference
geometry differs from the source volume grid, instead of silently re-gridding onto
it. Genuinely-empty sources are still tolerated.

Review your project's call sites for these three functions:

- If a mismatch there was always a bug (stale/foreign segmentation reaching the
  set-op), the new default is correct — fix the caller to conform the input first
  (see step 4) rather than suppress the error.
- If a call site intentionally relied on the old silent re-grid — e.g. deliberately
  combining segmentations from a divergent earlier-epoch grid — pass `resample=True`
  to keep the legacy behavior.

## 6. Conversion-orientation epoch (grid canonicalization)

A later fix changes the DICOM→NIfTI converter's on-disk grid layout for every
newly-converted volume (see the CHANGELOG's *Breaking* entry for the exact
release this landed in): the in-plane axis order now follows
`ImageOrientationPatient` order end-to-end (no more internal row/column swap),
and the canonical slice sense is now the side of the IOP normal instead of a
fixed +dominant-axis convention. The change is always an exact,
physically-equivalent index rearrangement — never a mirror, never a
shape/rotation change — but it means a segmentation painted against a
pre-epoch volume no longer shares an index grid with the same series
re-converted after upgrading. Full design rationale and probe evidence:
[`docs/grid-workflows.md`](../../docs/grid-workflows.md).

### Detect

Two complementary, framework-level primitives (no project-specific script
needed to detect):

```python
from clarinet.services.image import RelationKind, grid_relation, read_grid

relation = grid_relation(read_grid(seg_path), read_grid(volume_path))
if relation.kind is not RelationKind.SAME:
    ...  # REARRANGED: same series, different-epoch grid. FOREIGN: unrelated grid.
```

`is_volume_misoriented(volume_nifti, dicom_dir)` (used in step 2 above) also
covers this epoch — it still raises `OrientationUnverifiable` rather than
guessing when ground truth can't be established.

### Repair

Idempotent, and exact for the `REARRANGED` case this epoch produces (a signed
permutation — nearest-neighbour resampling lands precisely on voxel centers, no
blur):

```python
from clarinet.services.image import conform_seg_to_grid

conform_seg_to_grid(seg_path, volume_path)  # in place; or out_path=... to copy
```

Handles both a single-array 3-D segmentation and a 4-D layered Slicer
`.seg.nrrd` (segment names/label values/layers preserved). Raises
`GeometryMismatchError` instead of resampling if the pair turns out to be
genuinely unrelated (`FOREIGN`) — pass `allow_resample=True` only once that has
been confirmed intentional.

### Guard future exports

Any Slicer script that exports a segmentation against a volume should pass
`conform_to=<volume file path>` to `export_segmentation` — this both repairs a
`REARRANGED` node transparently and refuses (rather than silently mis-exports)
a `FOREIGN` one. See
[`.claude/rules/slicer-helper-api.md`](../../.claude/rules/slicer-helper-api.md).

### Also: pre-2026-03-08 clarinet NRRDs may now fail to read

Independently of the epoch above, a clarinet-written NRRD (including a
`.seg.nrrd`) saved **before 2026-03-08** can carry `space directions` without a
`space` field. `Image.read_nrrd`/`LayeredSegmentation.read_header` now honor
the `space` field strictly and raise `ImageReadError` on that combination
instead of silently assuming LPS. Every clarinet/Slicer-authored NRRD has
always physically been LPS, so the fix is a one-time header patch, not a
geometry change — read and rewrite the header directly with `pynrrd` (not
through `Image`, which is exactly what now raises on this file):

```python
import nrrd

data, header = nrrd.read(str(seg_path))
header.setdefault("space", "left-posterior-superior")
nrrd.write(str(seg_path), data, header)
```

After this one-time re-save the file reads normally through `Image` /
`LayeredSegmentation` / `read_grid` again.
