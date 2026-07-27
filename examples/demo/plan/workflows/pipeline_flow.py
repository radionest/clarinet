"""Pipeline flow: NDT comparative defect-detection study.

Workflow for multi-modality defect segmentation, master model management,
projection comparison, and second review.

See README.md for full business logic description.

This version uses the implemented RecordFlow/Pipeline DSL.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from clarinet_plan.definitions.record_types import master_model, master_projection, segmentation
from clarinet_plan.utils.seg_utils import master_label_converter, save_seg_nrrd

from clarinet.services.image import Segmentation
from clarinet.services.pipeline import (
    PipelineMessage,
    SyncTaskContext,
    TaskContext,
    pipeline_task,
)
from clarinet.services.recordflow import Field, file, record, study
from clarinet.settings import settings
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from clarinet.client import ClarinetClient
    from clarinet.models.record import RecordRead

F = Field()

# ---------------------------------------------------------------------------
# Pipeline tasks (run in workers)
# ---------------------------------------------------------------------------


@pipeline_task()
def init_master_model(_msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    """Build the master model from the first completed CT segmentation.

    Takes the inspector's segmentation, binarizes all non-zero voxels,
    splits into connected components with unique numbers, and saves
    as .seg.nrrd with named segments.
    """
    if ctx.files.exists(master_model):
        return

    seg_path = ctx.files.resolve(segmentation)
    if not seg_path.is_file():
        raise FileNotFoundError(
            f"Segmentation file not found: {seg_path} — file may not have been saved yet"
        )
    master_path = ctx.files.resolve(master_model)

    from skimage.measure import label

    seg = Segmentation(autolabel=False)
    seg.read(seg_path)
    # Binarize: all categories (defect/indeterminate/cosmetic) → single foreground
    labeled = label(seg.img > 0).astype(np.uint8)
    unique = sorted(int(lbl) for lbl in np.unique(labeled) if lbl != 0)
    names = [str(lbl) for lbl in unique]
    Path(master_path).parent.mkdir(parents=True, exist_ok=True)
    save_seg_nrrd(
        labeled,
        master_path,
        names,
        master_label_converter,
        spacing=seg.spacing,
        origin=seg._origin,
        direction=seg._direction,
    )


@pipeline_task()
async def auto_project_ct(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Auto-create the projection for CT — a copy of the master model.

    For CT studies, the projection matches the master model (same
    coordinate system). For non-CT studies the record stays pending
    for the expert.
    """
    assert msg.record_id is not None

    # Determine study type from first_check
    first_checks = await ctx.records.find("first-check", study_uid=msg.study_uid)
    study_type = (first_checks[0].data or {}).get("study_type") if first_checks else None
    if study_type != "CT":
        return  # Not CT → leave pending for the expert

    master_path = ctx.files.resolve(master_model)
    proj_path = ctx.files.resolve(master_projection)
    Path(proj_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(master_path), str(proj_path))

    await ctx.client.submit_record_data(msg.record_id, {})


@pipeline_task(auto_submit=True)
def compare_w_projection(msg: PipelineMessage, ctx: SyncTaskContext) -> dict[str, Any]:
    """Automatically compare the inspector's segmentation with the master model projection.

    For each ROI, checks for overlap:
    - overlap exists → same defect
    - no overlap → false negative or false positive

    Returns dict with comparison results — auto-submitted via ``auto_submit``.
    """
    assert msg.record_id is not None

    seg_path = ctx.files.resolve(segmentation)
    proj_path = ctx.files.resolve(master_projection)

    # Binarize the inspector's segmentation before labeling:
    # defect/indeterminate/cosmetic → single foreground → connected components
    raw = Segmentation(autolabel=False)
    raw.read(seg_path)
    seg = Segmentation(autolabel=True)
    seg.img = (raw.img > 0).astype(np.uint8)

    proj = Segmentation(autolabel=False)  # preserve master model labels
    proj.read(proj_path)

    # Defects on the projection not found by the inspector
    fn = proj.difference(seg)
    false_negative = [{"defect_num": int(lbl)} for lbl in np.unique(fn.img) if lbl != 0]

    # Inspector's defects absent from the projection
    fp = seg.difference(proj)

    return {
        "false_negative": false_negative,
        "false_negative_num": len(false_negative),
        "false_positive_num": fp.count,
    }


@pipeline_task(queue=settings.dicom_queue_name)
async def anonymize_study_with_type(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Anonymize the study and tag the Record with the project's study_type.

    Thin wrapper over the framework's :func:`run_anonymization` helper that
    looks up ``study_type`` from the related ``first-check`` record (project
    knowledge) and passes it as ``extra_record_data`` so it lands in the
    anonymize-study Record's ``data`` payload.

    Must NOT be named ``anonymize_study_pipeline``: task names are
    ``{namespace}:{function_name}``, so re-using the built-in's name makes
    ``register_task()`` raise ``PipelineConfigError`` once ``have_dicom`` is on.
    """
    from clarinet.services.dicom.pipeline import run_anonymization

    first_checks = await ctx.records.find("first-check", study_uid=msg.study_uid)
    first_data = first_checks[0].data if first_checks else None
    study_type = first_data.get("study_type") if first_data else None

    await run_anonymization(msg, ctx, extra_record_data={"study_type": study_type})


async def create_projection_record(
    record: RecordRead,
    context: dict[str, Any],  # noqa: ARG001
    client: ClarinetClient,
) -> None:
    """Create ``create-master-projection`` with series_uid from first-check."""
    first_checks = await client.find_records(
        record_type_name="first-check",
        study_uid=record.study_uid,
    )
    if not first_checks:
        logger.warning(f"No first-check for study {record.study_uid}, skipping projection")
        return

    best_series = (first_checks[0].data or {}).get("best_series")
    if not best_series:
        logger.warning(f"No best_series in first-check for study {record.study_uid}")
        return

    from clarinet.models import RecordCreate

    await client.create_record(
        RecordCreate(
            record_type_name="create-master-projection",
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=best_series,
            context_info=(
                f"Created by flow from record {record.record_type.name} (id={record.id})"
            ),
        )
    )


async def create_comparison_record(
    record: RecordRead,
    context: dict[str, Any],  # noqa: ARG001
    client: ClarinetClient,
) -> None:
    """Create ``compare-with-projection`` linked to the segmentation as parent."""
    first_checks = await client.find_records(
        record_type_name="first-check",
        study_uid=record.study_uid,
    )
    best_series = (first_checks[0].data or {}).get("best_series") if first_checks else None
    if not best_series:
        logger.warning(f"No best_series in first-check for study {record.study_uid}")
        return

    from clarinet.models import RecordCreate

    await client.create_record(
        RecordCreate(
            record_type_name="compare-with-projection",
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=best_series,
            parent_record_id=record.id,
            context_info=(f"Created by flow from {record.record_type.name} (id={record.id})"),
        )
    )


async def unblock_comparisons(
    record: RecordRead,
    context: dict[str, Any],  # noqa: ARG001
    client: ClarinetClient,
) -> None:
    """Check-files on all blocked ``compare-with-projection`` for this series."""
    comparisons = await client.find_records(
        series_uid=record.series_uid,
        record_status="blocked",
    )
    for comp in comparisons:
        await client.check_record_files(comp.id)


async def create_second_review_record(
    record: RecordRead,
    context: dict[str, Any],  # noqa: ARG001
    client: ClarinetClient,
) -> None:
    """Create ``second-review`` linked to parent segmentation for ``{user_id}`` resolution.

    The ``parent_record_id`` is set to the compare-with-projection's parent
    (the segmentation record), which has ``user_id`` — enabling the
    ``{user_id}`` pattern placeholder in second-review's file definitions.
    """
    from clarinet.models import RecordCreate

    await client.create_record(
        RecordCreate(
            record_type_name="second-review",
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=record.series_uid,
            parent_record_id=record.parent_record_id,
            context_info=f"Created from compare-with-projection (id={record.id})",
        )
    )


async def unblock_second_reviews(
    record: RecordRead,
    context: dict[str, Any],  # noqa: ARG001
    client: ClarinetClient,
) -> None:
    """Check-files on all blocked ``second-review`` for this series."""
    reviews = await client.find_records(
        record_type_name="second-review",
        series_uid=record.series_uid,
        record_status="blocked",
    )
    for review in reviews:
        await client.check_record_files(review.id)


async def create_repair_report(
    record: RecordRead,
    context: dict[str, Any],  # noqa: ARG001
    client: ClarinetClient,
) -> None:
    """Create ``repair-report`` prefilled with defects from repair-plan."""
    from clarinet.models import RecordCreate

    plan_data = record.data or {}
    raw_defects = plan_data.get("defects")
    if not isinstance(raw_defects, list):
        raw_defects = []

    prefill: dict[str, Any] = {
        "defects": [
            {"defect_num": defect["defect_num"], "cluster": defect.get("cluster")}
            for defect in raw_defects
            if isinstance(defect, dict) and "defect_num" in defect
        ],
        "additional_defects": [],
    }

    await client.create_record(
        RecordCreate(
            record_type_name="repair-report",
            patient_id=record.patient_id,
            data=prefill,
            context_info=f"Created by flow from repair-plan (id={record.id})",
        )
    )


async def dispatch_nifti_conversion(
    record: RecordRead,
    context: dict[str, Any],  # noqa: ARG001
    client: ClarinetClient,  # noqa: ARG001
) -> None:
    """Dispatch DICOM→NIfTI conversion for the best series from first-check."""
    best_series = (record.data or {}).get("best_series")
    if not best_series:
        logger.warning(f"No best_series in first-check for study {record.study_uid}")
        return

    from clarinet.services.pipeline.tasks.convert_series import convert_series_to_nifti

    msg = PipelineMessage(
        patient_id=record.patient_id,
        study_uid=record.study_uid,
        series_uid=best_series,
    )
    await convert_series_to_nifti.kicker().kiq(msg.model_dump())
    logger.info(f"Dispatched NIfTI conversion for series {best_series}")


async def create_view_nifti_record(
    record: RecordRead,
    context: dict[str, Any],  # noqa: ARG001
    client: ClarinetClient,
) -> None:
    """Create ``view-nifti`` record for the best series from first-check."""
    best_series = (record.data or {}).get("best_series")
    if not best_series:
        return

    from clarinet.models import RecordCreate

    await client.create_record(
        RecordCreate(
            record_type_name="view-nifti",
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=best_series,
            context_info=f"Created by flow from first-check (id={record.id})",
        )
    )


# ---------------------------------------------------------------------------
# Flow: record creation from first-check results
# ---------------------------------------------------------------------------

# A new study arriving creates a first-check
(study().on_creation().create_record("first-check"))

# first-check → anonymize-study (instead of direct segmentation creation)
(record("first-check").on_finished().if_record(F.is_good == True).create_record("anonymize-study"))

# first-check → DICOM→NIfTI conversion for the best series
(record("first-check").on_finished().if_record(F.is_good == True).call(dispatch_nifti_conversion))

# first-check → create view-nifti record
# (blocking on volume.nii.gz happens later when the task resolves FileRef)
(record("first-check").on_finished().if_record(F.is_good == True).call(create_view_nifti_record))

# Run anonymization on creation
(
    record("anonymize-study")
    .on_status("pending")
    .do_task(anonymize_study_with_type, send_to_pacs=True)
)

# After anonymization → create segmentations by study_type
(
    record("anonymize-study")
    .on_finished()
    .match(F.study_type)
    .case("CT")
    .create_record("segment-ct-single", "segment-ct-with-archive")
    .case("UT")
    .create_record("segment-ut-single")
    .case("CT-HD")
    .create_record("segment-ct-hd-single")
    .case("UT-HD")
    .create_record("segment-ut-hd-single")
    .case("MCT")
    .create_record("segment-mct-single")
)

# ---------------------------------------------------------------------------
# Flow: after segmentation completes
# ---------------------------------------------------------------------------

# All single-variant segmentation types
SEGMENT_TYPES = [
    "segment-ct-single",
    "segment-ut-single",
    "segment-ct-hd-single",
    "segment-ut-hd-single",
    "segment-mct-single",
]

for seg_type in SEGMENT_TYPES:
    # Create the master model projection onto the segmentation's series
    (record(seg_type).on_finished().call(create_projection_record))
    # Each segmentation creates a compare-with-projection, bound to itself as parent
    (record(seg_type).on_finished().call(create_comparison_record))

# segment-ct-with-archive also triggers projection and comparison
(record("segment-ct-with-archive").on_finished().call(create_projection_record))
(record("segment-ct-with-archive").on_finished().call(create_comparison_record))

# Build the master model from the first completed CT-with-archive segmentation
(record("segment-ct-with-archive").on_finished().do_task(init_master_model))

# ---------------------------------------------------------------------------
# Flow: auto-projection for CT on record creation
# ---------------------------------------------------------------------------

(record("create-master-projection").on_status("pending").do_task(auto_project_ct))

# ---------------------------------------------------------------------------
# Flow: after projection completes -> unblock comparisons
# ---------------------------------------------------------------------------

(record("create-master-projection").on_finished().call(unblock_comparisons))
(record("create-master-projection").on_finished().call(unblock_second_reviews))

# Auto-fill compare-with-projection on creation (role=auto)
(record("compare-with-projection").on_status("pending").do_task(compare_w_projection))

# ---------------------------------------------------------------------------
# Flow: reacting to comparison results
# ---------------------------------------------------------------------------

# false_positive > 0 -> create update-master-model
(
    record("compare-with-projection")
    .on_finished()
    .if_record(F.false_positive_num > 0)
    .create_record("update-master-model")
)

# Any discrepancy -> second-review for the inspector (callback for parent_record_id)
(
    record("compare-with-projection")
    .on_finished()
    .if_record(F.false_negative_num > 0)
    .call(create_second_review_record)
)

# ---------------------------------------------------------------------------
# Flow: invalidate projections when the master model is updated
# ---------------------------------------------------------------------------

(file("master_model").on_update().invalidate_all_records("create-master-projection"))

# ---------------------------------------------------------------------------
# Flow: stages 10-14 (MRB → repair → metallography)
# ---------------------------------------------------------------------------

# MRB conclusion → repair-model (expert creates 3D model)
(record("mrb-conclusion").on_finished().create_record("repair-model"))

# repair-model → repair-plan (expert plans repair zones)
(record("repair-model").on_finished().create_record("repair-plan"))

# repair-plan → repair-report (prefilled with defect list for technician)
(record("repair-plan").on_finished().call(create_repair_report))

# In-process: if additional defects found → update master model
(
    record("repair-protocol")
    .on_finished()
    .if_record(F.additionally_found > 0)
    .create_record("update-master-model")
)

# Note: retrospective-characterization (stage 8) — created manually by coordinator
# after a blind-reassessment interval (no automatic trigger)
