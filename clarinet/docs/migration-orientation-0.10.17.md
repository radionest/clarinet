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
