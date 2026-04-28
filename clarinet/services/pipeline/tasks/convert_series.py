"""Built-in pipeline task: DICOM series → NIfTI volume conversion.

Downloads a DICOM series from PACS via C-GET, converts to NIfTI with
correct affine (spacing, origin, direction), and saves as ``volume.nii.gz``
in the series working directory.

The task is triggered from RecordFlow via a ``.call()`` callback that
provides ``series_uid`` in the ``PipelineMessage``.  It does **not** require
a ``record_id`` — ``build_task_context`` falls back to building working
directories from the series.

Idempotency: if the output file already exists and is non-empty,
the task is skipped.
"""

from __future__ import annotations

import tempfile
from asyncio import to_thread
from pathlib import Path

from clarinet.config.primitives import FileDef
from clarinet.exceptions.domain import PipelineStepError
from clarinet.services.pipeline.context import TaskContext
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.task import pipeline_task
from clarinet.settings import settings
from clarinet.utils.logger import logger

VOLUME_NIFTI = FileDef(
    pattern="volume.nii.gz",
    level="SERIES",
    description="NIfTI volume converted from DICOM series",
)
"""Canonical FileDef for the NIfTI output.

Duplicated from project-level ``record_types.py`` to keep the domain task
independent of any specific project configuration.  ``FileResolver`` uses
``pattern`` and ``level``, not object identity.
"""


async def _convert_series_impl(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Core conversion logic — testable without TaskIQ broker.

    Raises:
        PipelineStepError: If ``series_uid`` is missing, C-GET returns 0 instances,
            or DICOM read / NIfTI write fails.
    """
    series_uid = msg.series_uid
    if not series_uid:
        raise PipelineStepError(
            "convert_series_to_nifti",
            "series_uid is required — ensure the task is triggered with "
            "a PipelineMessage that has series_uid set",
        )

    # Resolve output path — works even without record_id because
    # build_task_context falls back to series_uid → build_working_dirs_from_series
    output_path = ctx.files.resolve(VOLUME_NIFTI)

    # Idempotency: skip if file exists and is non-empty
    if output_path.is_file() and output_path.stat().st_size > 0:
        from clarinet.utils.file_checksums import compute_file_checksum

        checksum = await compute_file_checksum(output_path)
        logger.info(
            f"NIfTI already exists at {output_path} "
            f"(sha256={checksum[:12] if checksum else '?'}…), skipping conversion"
        )
        return

    # 1. C-GET series to temp directory
    from clarinet.services.dicom import DicomClient, DicomNode

    with tempfile.TemporaryDirectory() as tmpdir:
        client = DicomClient(
            calling_aet=settings.dicom_aet,
            max_pdu=settings.dicom_max_pdu,
        )
        pacs = DicomNode(
            aet=settings.pacs_aet,
            host=settings.pacs_host,
            port=settings.pacs_port,
        )
        result = await client.get_series(
            study_uid=msg.study_uid,
            series_uid=series_uid,
            peer=pacs,
            output_dir=Path(tmpdir),
        )

        if result.num_completed == 0:
            raise PipelineStepError(
                "convert_series_to_nifti",
                f"C-GET returned 0 instances for series {series_uid} (study {msg.study_uid})",
            )

        logger.info(f"C-GET completed: {result.num_completed} instances for series {series_uid}")

        # 2. Read DICOM series → Image with spacing/origin/direction
        from clarinet.exceptions.domain import ImageReadError, ImageWriteError
        from clarinet.services.image import FileType, Image

        img = Image()
        try:
            await to_thread(img.read_dicom_series, Path(tmpdir))
        except ImageReadError as exc:
            logger.error(f"DICOM read failed: {exc}")
            raise PipelineStepError("convert_series_to_nifti", f"DICOM read failed: {exc}") from exc

        # 3. Save as NIfTI
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await to_thread(img.save_as, output_path, FileType.NIFTI)
        except ImageWriteError as exc:
            logger.error(f"NIfTI save failed: {exc}")
            raise PipelineStepError("convert_series_to_nifti", f"NIfTI save failed: {exc}") from exc

    logger.info(f"Saved NIfTI volume to {output_path}")


@pipeline_task(queue=settings.dicom_queue_name)
async def convert_series_to_nifti(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Download DICOM series from PACS and convert to NIfTI.

    Uses ``msg.series_uid`` (set by the dispatching callback).
    Output: ``volume.nii.gz`` in the series working directory.

    Raises:
        PipelineStepError: If ``series_uid`` is missing or C-GET returns 0 instances.
    """
    await _convert_series_impl(msg, ctx)
