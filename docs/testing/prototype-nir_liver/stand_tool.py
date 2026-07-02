"""VM-side toolbox for the stand workflow test — runs INSIDE /opt/clarinet venv.

The host-side pytest harness drives the HTTP API; whenever it needs something
that only exists on the VM's disk (resolve a record's on-disk file path, read a
NIfTI's geometry, synthesise a ``.seg.nrrd`` / liver mask that matches that
geometry) it shells in and calls one subcommand here.

Why a VM-side tool instead of computing paths on the host: path rendering
depends on the project's ``disk_path_template`` + the record's anonymised UIDs,
and is owned by ``FileRepository``. Re-deriving it on the host would duplicate
framework logic and drift. Running the framework's own resolver on the box that
has the exact settings is the robust choice.

Run from ``/opt/clarinet`` (so ``settings.toml`` + ``settings.custom.toml`` and
``config_tasks_path=./plan`` resolve):

    /opt/clarinet/venv/bin/python stand_tool.py resolve <record_id> <file_name>
    /opt/clarinet/venv/bin/python stand_tool.py geometry <nifti_path>
    /opt/clarinet/venv/bin/python stand_tool.py make-seg <record_id> <file_name> \
        --ref-nifti <volume.nii.gz> --classes mts,unclear --seed 1
    /opt/clarinet/venv/bin/python stand_tool.py make-liver <record_id> <file_name> \
        --ref-nifti <volume.nii.gz>

Every subcommand prints a single JSON object to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import nrrd
import numpy as np
import SimpleITK as sitk

# Class label maps mirror clarinet_plan.utils.seg_utils.SEG_LABELS.
CLASS_LABELS = {"mts": 1, "unclear": 2, "benign": 3}


def _emit(obj: dict) -> None:
    print(json.dumps(obj))


async def _resolve_path(record_id: int, file_name: str) -> str:
    """Absolute on-disk path of *file_name* for *record_id* via FileRepository."""
    from clarinet.models.record import RecordRead
    from clarinet.repositories.file_repository import FileRepository
    from clarinet.repositories.record_repository import RecordRepository
    from clarinet.utils.db_manager import db_manager

    async with db_manager.get_async_session_context() as session:
        record = await RecordRepository(session).get_with_relations(record_id)
        record_read = RecordRead.model_validate(record)
        return str(FileRepository(record_read).resolve_file(file_name))


def _read_geometry(nifti_path: str) -> dict:
    img = sitk.ReadImage(nifti_path)
    arr = sitk.GetArrayFromImage(img)  # (z, y, x)
    return {
        "shape_zyx": list(arr.shape),
        "spacing": list(img.GetSpacing()),  # (x, y, z)
        "origin": list(img.GetOrigin()),
        "direction": list(img.GetDirection()),  # 9-tuple row-major 3x3
    }


def _save_seg_nrrd(
    data_xyz: np.ndarray,
    path: str,
    segment_names: list[str],
    label_values: list[int],
    *,
    spacing: tuple[float, float, float],
    origin: tuple[float, float, float],
    direction: np.ndarray,
) -> None:
    """Write a 3D uint8 label array as a Slicer-style ``.seg.nrrd``.

    Header layout matches ``clarinet_plan.utils.seg_utils.save_seg_nrrd`` so the
    pipeline's readers (``_load_seg_flat`` / ``read_seg_nrrd_labels``) parse it
    identically to a real project-produced file.
    """
    header = {
        "type": "unsigned char",
        "dimension": 3,
        "space": "left-posterior-superior",
        "space directions": (direction * np.array(spacing)).T,
        "space origin": np.array(origin),
    }
    for i, (name, lbl) in enumerate(zip(segment_names, label_values, strict=True)):
        header[f"Segment{i}_ID"] = f"Segment_{i}"
        header[f"Segment{i}_Name"] = name
        header[f"Segment{i}_LabelValue"] = str(lbl)
        header[f"Segment{i}_Layer"] = "0"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    nrrd.write(path, data_xyz.astype(np.uint8), header)


def _blob(shape_xyz, center, radius) -> np.ndarray:
    """Boolean sphere mask on an (x, y, z) grid."""
    zz, yy, xx = np.ogrid[: shape_xyz[2], : shape_xyz[1], : shape_xyz[0]]
    # center is (cx, cy, cz); grids above are z,y,x — align explicitly.
    d2 = (xx - center[0]) ** 2 + (yy - center[1]) ** 2 + (zz - center[2]) ** 2
    mask_zyx = d2 <= radius**2
    return np.transpose(mask_zyx, (2, 1, 0))  # → (x, y, z)


def _geometry_for_seg(nifti_path: str):
    img = sitk.ReadImage(nifti_path)
    arr = sitk.GetArrayFromImage(img)  # (z, y, x)
    shape_xyz = (arr.shape[2], arr.shape[1], arr.shape[0])
    spacing = img.GetSpacing()
    origin = img.GetOrigin()
    direction = np.array(img.GetDirection()).reshape(3, 3)
    return shape_xyz, spacing, origin, direction


def cmd_resolve(args) -> None:
    path = asyncio.run(_resolve_path(args.record_id, args.file_name))
    _emit({"path": path, "exists": os.path.exists(path)})


def cmd_geometry(args) -> None:
    _emit(_read_geometry(args.nifti_path))


def cmd_make_seg(args) -> None:
    """Synthesise a lesion ``.seg.nrrd`` matching the reference volume geometry.

    One disjoint sphere per requested class, placed along the central axis so
    blobs never touch (distinct connected components → distinct master lesions,
    and no cross-class contact that the segment validator would reject).
    """
    out_path = asyncio.run(_resolve_path(args.record_id, args.file_name))
    shape_xyz, spacing, origin, direction = _geometry_for_seg(args.ref_nifti)
    classes = [c.strip() for c in args.classes.split(",") if c.strip()]

    data = np.zeros(shape_xyz, dtype=np.uint8)
    names: list[str] = []
    values: list[int] = []
    cx, cy, cz = shape_xyz[0] // 2, shape_xyz[1] // 2, shape_xyz[2] // 2
    radius = max(2, min(shape_xyz) // 12)
    step = radius * 3
    rng = np.random.default_rng(args.seed)
    for i, cls in enumerate(classes):
        offset = (i - (len(classes) - 1) / 2) * step
        jitter = rng.integers(-1, 2)
        center = (cx + int(offset) + jitter, cy, cz)
        lbl = CLASS_LABELS[cls]
        data[_blob(shape_xyz, center, radius)] = lbl
        names.append(cls)
        values.append(lbl)

    _save_seg_nrrd(
        data, out_path, names, values, spacing=spacing, origin=origin, direction=direction
    )
    _emit({"path": out_path, "lesions": len(classes), "labels": values})


def cmd_make_blob_seg(args) -> None:
    """Synthesise a ``.seg.nrrd`` of one big ellipsoid with a given label.

    Used for the anatomy model (liver parenchyma) and resection clusters: a
    central ellipsoid large enough to overlap the master-model lesions, so
    ``compute_lesion_cluster_mapping`` assigns every lesion to this cluster and
    ``merge_seg_nrrd`` (combine-resection) gets a consistent grid.
    """
    out_path = asyncio.run(_resolve_path(args.record_id, args.file_name))
    shape_xyz, spacing, origin, direction = _geometry_for_seg(args.ref_nifti)
    cz, cy, cx = (s / 2 for s in (shape_xyz[2], shape_xyz[1], shape_xyz[0]))
    rx, ry, rz = (max(2.0, s * args.radius_frac) for s in shape_xyz)
    zz, yy, xx = np.ogrid[: shape_xyz[2], : shape_xyz[1], : shape_xyz[0]]
    ell_zyx = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 + ((zz - cz) / rz) ** 2 <= 1.0
    data = np.transpose(ell_zyx, (2, 1, 0)).astype(np.uint8) * args.label
    _save_seg_nrrd(
        data,
        out_path,
        [str(args.label)],
        [args.label],
        spacing=spacing,
        origin=origin,
        direction=direction,
    )
    _emit({"path": out_path, "label": args.label, "voxels": int((data > 0).sum())})


def cmd_make_liver(args) -> None:
    """Synthesise a binary liver mask (large ellipsoid) as a ``.nii.gz``.

    Stands in for the GPU TotalSegmentator output so anatomy-model can unblock
    without a GPU worker. Geometry copied verbatim from the reference volume.
    """
    out_path = asyncio.run(_resolve_path(args.record_id, args.file_name))
    img = sitk.ReadImage(args.ref_nifti)
    arr = sitk.GetArrayFromImage(img)  # (z, y, x)
    zz, yy, xx = np.indices(arr.shape)
    cz, cy, cx = (s / 2 for s in arr.shape)
    rz, ry, rx = (max(2.0, s / 3) for s in arr.shape)
    ell = ((zz - cz) / rz) ** 2 + ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1.0
    mask = sitk.GetImageFromArray(ell.astype(np.uint8))
    mask.CopyInformation(img)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(mask, out_path)
    _emit({"path": out_path, "voxels": int(ell.sum())})


def main() -> None:
    parser = argparse.ArgumentParser(description="Stand workflow VM-side toolbox")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("resolve")
    p.add_argument("record_id", type=int)
    p.add_argument("file_name")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("geometry")
    p.add_argument("nifti_path")
    p.set_defaults(func=cmd_geometry)

    p = sub.add_parser("make-seg")
    p.add_argument("record_id", type=int)
    p.add_argument("file_name")
    p.add_argument("--ref-nifti", required=True)
    p.add_argument("--classes", default="mts")
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(func=cmd_make_seg)

    p = sub.add_parser("make-blob-seg")
    p.add_argument("record_id", type=int)
    p.add_argument("file_name")
    p.add_argument("--ref-nifti", required=True)
    p.add_argument("--label", type=int, default=1)
    p.add_argument("--radius-frac", type=float, default=0.45)
    p.set_defaults(func=cmd_make_blob_seg)

    p = sub.add_parser("make-liver")
    p.add_argument("record_id", type=int)
    p.add_argument("file_name")
    p.add_argument("--ref-nifti", required=True)
    p.set_defaults(func=cmd_make_liver)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        _emit({"error": f"{type(exc).__name__}: {exc}"})
        sys.exit(1)


if __name__ == "__main__":
    main()
