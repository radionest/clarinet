# Image Processing Service

## Modules

| File | Purpose |
|---|---|
| `image.py` | `Image` class — base NIfTI/NRRD/DICOM I/O with spacing/shape |
| `segmentation.py` | `Segmentation(Image)` — labeled masks, morphology, set operations, ROI filtering |
| `layered_segmentation.py` | `LayeredSegmentation` — 4-D `(L,X,Y,Z)` overlapping-segment NRRD (Slicer format) over one shared 3-D grid |
| `dicom_volume.py` | DICOM series → 3D numpy volume (used by `Image.read_dicom_series()`) |
| `coco2nii.py` | COCO JSON polygon annotations → `Segmentation` |
| `correspondence/` | Pluggable component-correspondence engine (measures x matching x set operations) backing the Segmentation set ops |

## Supported Formats

- **NIfTI** (.nii, .nii.gz): read + write via nibabel
- **NRRD** (.nrrd): read + write via pynrrd
- **DICOM** series: read-only via SimpleITK/GDCM (write requires UID generation — out of scope)

## Key Design

- **Synchronous API**: all operations are CPU-bound; wrap with `asyncio.to_thread()` in pipeline tasks
- **Segmentation dtype**: always `np.uint8` (max 255 labels)
- **Isotropic threshold**: `_ISOTROPIC_Z_THRESHOLD = 200` — volumes with Z < 200 use spacing-aware morphology; larger volumes use ball structuring element for performance
- **Exceptions**: `ImageError`, `ImageReadError`, `ImageWriteError` from `clarinet.exceptions.domain`
- **RAM-lean reads (opt-in)**: `read(path, load_data=False)` → grid + `shape`/`has_data` only (the #452 lean path); `read(path, dtype=np.int16|bool|...)` casts once off-disk (no forced float64; `dtype=None` keeps float64). `read_slice(path, i, axis=2)` returns one 2-D slice; `dataobj` is a read-only NIfTI proxy. `unload()`/`close()`/`with Image() as im:` free the volume (and mmap) deterministically.
- **LayeredSegmentation** (`layered_segmentation.py`): 4-D `(L,X,Y,Z)` overlapping-segment NRRD (Slicer format) over one shared 3-D grid — composition, not a 4-D `Segmentation`. `from_layers().save()` (raw, layer/list-axis-first — Slicer-native, fill-in-place; layers interleaved on disk) + `read_header`/`read_layer`/`read_layer_slice`/`iter_layers`. Default write: one segment per layer, label 1.

## Dependencies

- `numpy`, `nibabel`, `scikit-image`, `pynrrd` (declared in `pyproject.toml` image extra)
- `SimpleITK` (DICOM series reader in `dicom_volume.py`); `pydicom` (used by anonymizer/dicomweb/dicom services)

## Integration with Pipeline

Pipeline tasks should call image service functions inside `asyncio.to_thread()`:
```python
volume = await asyncio.to_thread(image.read, path)
```

## Detailed Documentation

See [/docs/image-service.md](/docs/image-service.md) for full behavioral reference (cross-format spacing, set operation semantics, HU correction pipeline, COCO converter, DICOM reader).
