"""Background anonymization tasks: pipeline dispatch + in-process fallback.

Provides two execution paths for background anonymization:
- ``anonymize_study_task``: TaskIQ task dispatched via RabbitMQ (pipeline enabled).
- ``anonymize_study_background``: In-process ``asyncio.create_task`` target (pipeline disabled).

Both construct an ``AnonymizationService`` with a fresh DB session, avoiding the
closed-session bug that occurs when DI-scoped services are used in background tasks.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from clarinet.utils.logger import logger


@asynccontextmanager
async def _create_anonymization_service() -> AsyncGenerator:
    """Create an AnonymizationService with a fresh DB session.

    Yields:
        Configured AnonymizationService with its own session lifecycle.
    """
    from clarinet.repositories.patient_repository import PatientRepository
    from clarinet.repositories.series_repository import SeriesRepository
    from clarinet.repositories.study_repository import StudyRepository
    from clarinet.services.anonymization_service import AnonymizationService
    from clarinet.services.dicom.client import DicomClient
    from clarinet.services.dicom.models import DicomNode
    from clarinet.settings import settings
    from clarinet.utils.db_manager import db_manager

    async with db_manager.get_async_session_context() as session:
        study_repo = StudyRepository(session)
        patient_repo = PatientRepository(session)
        series_repo = SeriesRepository(session)

        dicom_client = DicomClient(
            calling_aet=settings.dicom_aet,
            max_pdu=settings.dicom_max_pdu,
        )
        pacs = DicomNode(
            aet=settings.pacs_aet,
            host=settings.pacs_host,
            port=settings.pacs_port,
        )

        yield AnonymizationService(
            study_repo=study_repo,
            patient_repo=patient_repo,
            series_repo=series_repo,
            dicom_client=dicom_client,
            pacs=pacs,
        )


def _get_task() -> Any:
    """Lazily define and return the broker task.

    Deferred to avoid importing TaskIQ at module level (optional dependency).

    Returns:
        The decorated ``anonymize_study_task`` broker task.
    """
    from clarinet.services.pipeline.broker import DICOM_QUEUE, get_broker

    broker = get_broker()

    @broker.task(task_name="anonymize_study", queue=DICOM_QUEUE)
    async def anonymize_study_task(
        study_uid: str,
        save_to_disk: bool | None = None,
        send_to_pacs: bool | None = None,
    ) -> dict[str, Any]:
        """TaskIQ task for RabbitMQ-dispatched anonymization.

        Args:
            study_uid: Study Instance UID to anonymize.
            save_to_disk: Override for save-to-disk setting.
            send_to_pacs: Override for send-to-PACS setting.

        Returns:
            Anonymization result as a dict.
        """
        async with _create_anonymization_service() as service:
            result = await service.anonymize_study(
                study_uid,
                save_to_disk=save_to_disk,
                send_to_pacs=send_to_pacs,
            )
        return result.model_dump()  # type: ignore[no-any-return]

    return anonymize_study_task


# Lazy singleton for the broker task
_task: Any = None


def get_anonymize_study_task() -> Any:
    """Get the anonymize_study broker task (lazy init).

    Returns:
        The TaskIQ-decorated task callable.
    """
    global _task
    if _task is None:
        _task = _get_task()
    return _task


async def anonymize_study_background(
    study_uid: str,
    save_to_disk: bool | None = None,
    send_to_pacs: bool | None = None,
) -> None:
    """In-process fallback for background anonymization.

    Used as an ``asyncio.create_task`` target when the pipeline is disabled.
    Creates a fresh DB session internally and catches all exceptions.

    Args:
        study_uid: Study Instance UID to anonymize.
        save_to_disk: Override for save-to-disk setting.
        send_to_pacs: Override for send-to-PACS setting.
    """
    try:
        async with _create_anonymization_service() as service:
            await service.anonymize_study(
                study_uid,
                save_to_disk=save_to_disk,
                send_to_pacs=send_to_pacs,
            )
        logger.info(f"Background anonymization completed for study {study_uid}")
    except Exception:
        logger.exception(f"Background anonymization failed for study {study_uid}")
