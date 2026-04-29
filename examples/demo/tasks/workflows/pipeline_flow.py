"""Pipeline flow: Liver metastasis segmentation study.

Workflow for multi-modality segmentation, master model management,
projection comparison, and second review.

See README.md for full business logic description.

This version uses the implemented RecordFlow/Pipeline DSL
(as opposed to demo_liver/ which uses aspirational syntax).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from record_types import master_model, master_projection, segmentation
from utils.seg_utils import master_label_converter, save_seg_nrrd

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
# Pipeline tasks (выполняются в воркерах)
# ---------------------------------------------------------------------------


@pipeline_task()
def init_master_model(_msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    """Создание мастер-модели по первой завершённой КТ-сегментации.

    Берёт сегментацию врача, бинаризует все ненулевые вокселы,
    разделяет на connected components с уникальными номерами,
    сохраняет как .seg.nrrd с именованными сегментами.
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
    # Бинаризация: все категории (mts/unclear/benign) → единый foreground
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
    """Авто-создание проекции для КТ — копия мастер-модели.

    Для КТ-исследований проекция совпадает с мастер-моделью (та же
    координатная система). Для не-КТ запись остаётся в pending для эксперта.
    """
    assert msg.record_id is not None

    # Определяем тип исследования из first_check
    first_checks = await ctx.records.find("first-check", study_uid=msg.study_uid)
    study_type = (first_checks[0].data or {}).get("study_type") if first_checks else None
    if study_type != "CT":
        return  # Не КТ → оставляем в pending для эксперта

    master_path = ctx.files.resolve(master_model)
    proj_path = ctx.files.resolve(master_projection)
    Path(proj_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(master_path), str(proj_path))

    await ctx.client.submit_record_data(msg.record_id, {})


@pipeline_task(auto_submit=True)
def compare_w_projection(msg: PipelineMessage, ctx: SyncTaskContext) -> dict[str, Any]:
    """Автоматическое сравнение сегментации врача с проекцией мастер-модели.

    Для каждого ROI проверяет пересечение:
    - пересечение есть → один и тот же очаг
    - пересечения нет → false negative или false positive

    Returns dict with comparison results — auto-submitted via ``auto_submit``.
    """
    assert msg.record_id is not None

    seg_path = ctx.files.resolve(segmentation)
    proj_path = ctx.files.resolve(master_projection)

    # Бинаризация сегментации врача перед labeling:
    # mts/unclear/benign → единый foreground → connected components
    raw = Segmentation(autolabel=False)
    raw.read(seg_path)
    seg = Segmentation(autolabel=True)
    seg.img = (raw.img > 0).astype(np.uint8)

    proj = Segmentation(autolabel=False)  # preserve master model labels
    proj.read(proj_path)

    # Очаги на проекции, не найденные врачом
    fn = proj.difference(seg)
    false_negative = [{"lesion_num": int(lbl)} for lbl in np.unique(fn.img) if lbl != 0]

    # Очаги врача, отсутствующие на проекции
    fp = seg.difference(proj)

    return {
        "false_negative": false_negative,
        "false_negative_num": len(false_negative),
        "false_positive_num": fp.count,
    }


@pipeline_task(queue=settings.dicom_queue_name)
async def anonymize_study_pipeline(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Anonymize the study and tag the Record with the project's study_type.

    Thin wrapper over the framework's :func:`run_anonymization` helper that
    looks up ``study_type`` from the related ``first-check`` record (project
    knowledge) and passes it as ``extra_record_data`` so it lands in the
    anonymize-study Record's ``data`` payload.
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


async def create_resection_report(
    record: RecordRead,
    context: dict[str, Any],  # noqa: ARG001
    client: ClarinetClient,
) -> None:
    """Create ``resection-report`` prefilled with lesions from resection-plan."""
    from clarinet.models import RecordCreate

    plan_data = record.data or {}
    raw_lesions = plan_data.get("lesions")
    if not isinstance(raw_lesions, list):
        raw_lesions = []

    prefill: dict[str, Any] = {
        "lesions": [
            {"lesion_num": lesion["lesion_num"], "cluster": lesion.get("cluster")}
            for lesion in raw_lesions
            if isinstance(lesion, dict) and "lesion_num" in lesion
        ],
        "additional_lesions": [],
    }

    await client.create_record(
        RecordCreate(
            record_type_name="resection-report",
            patient_id=record.patient_id,
            data=prefill,
            context_info=f"Created by flow from resection-plan (id={record.id})",
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
# Flow: создание записей по результатам first-check
# ---------------------------------------------------------------------------

# При поступлении нового исследования создаётся first-check
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
    .do_task(anonymize_study_pipeline, send_to_pacs=True)
)

# After anonymization → create segmentations by study_type
(
    record("anonymize-study")
    .on_finished()
    .match(F.study_type)
    .case("CT")
    .create_record("segment-ct-single", "segment-ct-with-archive")
    .case("MRI")
    .create_record("segment-mri-single")
    .case("CT-AG")
    .create_record("segment-ctag-single")
    .case("MRI-AG")
    .create_record("segment-mriag-single")
    .case("PDCT-AG")
    .create_record("segment-pdctag-single")
)

# ---------------------------------------------------------------------------
# Flow: после завершения сегментации
# ---------------------------------------------------------------------------

# Список всех типов сегментации (single-варианты)
SEGMENT_TYPES = [
    "segment-ct-single",
    "segment-mri-single",
    "segment-ctag-single",
    "segment-mriag-single",
    "segment-pdctag-single",
]

for seg_type in SEGMENT_TYPES:
    # Создание проекции мастер-модели на серию сегментации
    (record(seg_type).on_finished().call(create_projection_record))
    # Каждая сегментация создаёт compare-with-projection, привязанный к себе как parent
    (record(seg_type).on_finished().call(create_comparison_record))

# segment-ct-with-archive тоже запускает проекцию и сравнение
(record("segment-ct-with-archive").on_finished().call(create_projection_record))
(record("segment-ct-with-archive").on_finished().call(create_comparison_record))

# Создание мастер-модели по первой завершённой КТ-сегментации с архивом
(record("segment-ct-with-archive").on_finished().do_task(init_master_model))

# ---------------------------------------------------------------------------
# Flow: авто-проекция для КТ при создании записи
# ---------------------------------------------------------------------------

(record("create-master-projection").on_status("pending").do_task(auto_project_ct))

# ---------------------------------------------------------------------------
# Flow: после завершения проекции -> разблокировка сравнений
# ---------------------------------------------------------------------------

(record("create-master-projection").on_finished().call(unblock_comparisons))
(record("create-master-projection").on_finished().call(unblock_second_reviews))

# Автозаполнение compare-with-projection при создании (role=auto)
(record("compare-with-projection").on_status("pending").do_task(compare_w_projection))

# ---------------------------------------------------------------------------
# Flow: по результатам сравнения
# ---------------------------------------------------------------------------

# false_positive > 0 -> создать update-master-model
(
    record("compare-with-projection")
    .on_finished()
    .if_record(F.false_positive_num > 0)
    .create_record("update-master-model")
)

# Любые расхождения -> second-review для врача (callback for parent_record_id)
(
    record("compare-with-projection")
    .on_finished()
    .if_record(F.false_negative_num > 0)
    .call(create_second_review_record)
)

# ---------------------------------------------------------------------------
# Flow: инвалидация проекций при обновлении мастер-модели
# ---------------------------------------------------------------------------

(file("master_model").on_update().invalidate_all_records("create-master-projection"))

# ---------------------------------------------------------------------------
# Flow: стадии 10-14 (MDK → хирургия → гистология)
# ---------------------------------------------------------------------------

# MDK conclusion → resection-model (expert creates 3D model)
(record("mdk-conclusion").on_finished().create_record("resection-model"))

# resection-model → resection-plan (expert plans resection zones)
(record("resection-model").on_finished().create_record("resection-plan"))

# resection-plan → resection-report (prefilled with lesion list for surgeon)
(record("resection-plan").on_finished().call(create_resection_report))

# Intraop: if additional lesions found → update master model
(
    record("intraop-protocol")
    .on_finished()
    .if_record(F.additionally_found > 0)
    .create_record("update-master-model")
)

# Note: retrospective-semiotics (stage 8) — created manually by coordinator
# after 4-7 week washout period (no automatic trigger)
