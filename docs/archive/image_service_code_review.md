# Code Review: `clarinet/services/image/`

## Summary

Image service is a well-isolated synchronous library for medical image I/O and segmentation processing. Architecture is clean: no DB access, no HTTP endpoints, no DI coupling. Tests are thorough (48 cases). Documentation is excellent.

Below are the issues found, grouped by severity.

---
---

## MEDIUM: Design Issues


### 6. `rois_hu_correction` ignores isotropic threshold (`segmentation.py:124`)

```python
temp_img = dilation(self.img, footprint=ball(radius))
```

The `dilate()` method at line 87 uses `isotropic_dilation` for volumes with Z < 200, but `rois_hu_correction` always uses `ball(radius)`. This is an inconsistency — either the threshold logic applies everywhere, or it should be documented why HU correction is exempt.

### 7. `binary_open` triggers two autolabel passes (`segmentation.py:99-100`)

```python
self.img = isotropic_erosion(self.img > 0, radius, spacing=self.spacing)
self.img = isotropic_dilation(self.img > 0, radius, spacing=self.spacing)
```

Each assignment to `self.img` goes through the setter, running `label()` + `astype(uint8)`. The intermediate labeling after erosion is wasted. Could use a local variable for the intermediate step:
```python
eroded = isotropic_erosion(self.img > 0, radius, spacing=self.spacing)
self.img = isotropic_dilation(eroded > 0, radius, spacing=self.spacing)
```

### 8. Magic numbers in set operations

- `__and__`: overlap threshold `> 2` voxels (line 255)
- `__sub__`: overlap threshold `< 10` voxels (line 276)
- `rois_hu_correction`: opening `ball(2)` hardcoded (line 130)

Should be named constants or method parameters for clarity and configurability.

---

## LOW: Minor Issues

### 9. `COCOAnnotation.segmentation` untyped (`coco2nii.py:56`)

```python
segmentation: list
```

Should be `list[list[list[float]]]` or `list[Any]` for Pydantic validation and type safety.

### 10. Only first polygon rasterized (`coco2nii.py:146`)

```python
polygons = annotation.segmentation[0]
```

If a COCO annotation has multiple polygons (multi-part regions), only the first is used. The rest are silently dropped.

### 11. DICOM spacing false positive (`dicom_volume.py:116`)

```python
if slice_sp == 0.0 or (slice_sp == 1.0 and len(datasets) > 1):
```

If the computed spacing genuinely equals 1.0mm, this condition falsely triggers the `SliceThickness` fallback. Unlikely but possible.

### 12. Tests use private attributes (`test_image.py` throughout)

Multiple tests set `seg._spacing` and `hu._img` instead of using public API. Couples tests to implementation details.

### 13. `_ISOTROPIC_Z_THRESHOLD = 200` lacks rationale

The constant at `segmentation.py:25` has a comment explaining *what* it does but not *why* 200 was chosen (performance profiling? memory limits?).

---

## Positive Observations

- **Architecture**: Pure library, zero coupling to DB/API/DI
- **Error handling**: Custom exception hierarchy with proper chaining (`from e`)
- **DICOM tolerance**: Graceful degradation with mixed valid/invalid files
- **Test quality**: 48 tests covering unit + multi-step workflows, including known-limitation documentation
- **Documentation**: CLAUDE.md, behavioral reference, test docstrings all consistent and thorough
- **Type hints**: Complete on all public methods
- **Set operation semantics**: Well-documented label-based behavior with edge cases explained

---

## Recommended Priority

1. Fix `__sub__` always-true condition (bug)
2. Fix `_create_mask` axis order (potential bug, verify with real COCO data)
3. Add spacing propagation to `_save_nifti` / `_save_nrrd` (eliminates 4 known limitations)
4. Add `case _:` to `save_as` (defensive)
5. Extract magic numbers to constants (readability)
