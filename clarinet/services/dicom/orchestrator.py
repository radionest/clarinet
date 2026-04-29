"""Record-aware anonymization orchestrator.

Wraps :class:`AnonymizationService` with skip-guard, idempotent Patient
anonymization, and submission of :class:`AnonymizationResult` (or error)
to a tracking ``Record``. Used by the HTTP endpoint when a Record exists
and by the built-in :func:`anonymize_study_pipeline` pipeline task.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from clarinet.client import ClarinetAPIError, ClarinetClient
from clarinet.exceptions.domain import AnonymizationFailedError
from clarinet.models.base import RecordStatus
from clarinet.services.anonymization_service import AnonymizationService
from clarinet.services.dicom.models import AnonymizationResult
from clarinet.settings import settings
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from clarinet.models.study import StudyRead


class AnonymizationOrchestrator:
    """Wraps ``AnonymizationService`` with Record-aware bookkeeping."""

    def __init__(
        self,
        anon_service: AnonymizationService,
        client: ClarinetClient,
    ) -> None:
        self.anon_service = anon_service
        self.client = client

    async def run(
        self,
        study_uid: str,
        *,
        record_id: int | None = None,
        save_to_disk: bool | None = None,
        send_to_pacs: bool | None = None,
        extra_record_data: dict[str, Any] | None = None,
    ) -> AnonymizationResult:
        """Run anonymization with optional Record tracking.

        With ``record_id`` set: applies skip-guard, anonymizes the Patient
        (idempotent — 409 means already done), runs DICOM anonymization, and
        submits the resulting fields (or error) to the Record.

        Without ``record_id``: only ensures Patient.anon_name and runs DICOM
        anonymization — equivalent to a thin wrapper over
        :meth:`AnonymizationService.anonymize_study`.

        Args:
            study_uid: Study Instance UID.
            record_id: Tracking Record id. ``None`` skips all Record bookkeeping.
            save_to_disk: Override ``settings.anon_save_to_disk``.
            send_to_pacs: Override ``settings.anon_send_to_pacs``.
            extra_record_data: Project-specific fields merged into the Record
                ``data`` payload (success, skip, and error branches).

        Raises:
            AnonymizationFailedError: re-raised after the Record is marked
                ``failed`` so retry/DLQ middleware see the failure.
        """
        do_send = send_to_pacs if send_to_pacs is not None else settings.anon_send_to_pacs

        # Fetch once and reuse for skip-guard + Patient anonymization.
        study = await self.client.get_study(study_uid)

        if record_id is not None:
            already_anon_uid = await self._already_done(study, record_id, do_send)
            if already_anon_uid is not None:
                logger.info(f"Study {study_uid} already anonymized, skipping")
                await self._submit(
                    record_id,
                    {
                        "skipped": True,
                        "anon_study_uid": already_anon_uid,
                        **(extra_record_data or {}),
                    },
                )
                return AnonymizationResult(
                    study_uid=study_uid,
                    anon_study_uid=already_anon_uid,
                    series_count=0,
                    series_anonymized=0,
                    series_skipped=0,
                    instances_anonymized=0,
                    instances_failed=0,
                    sent_to_pacs=False,
                )

        await self._ensure_patient_anonymized(study.patient_id)

        try:
            result = await self.anon_service.anonymize_study(
                study_uid,
                save_to_disk=save_to_disk,
                send_to_pacs=send_to_pacs,
            )
        except AnonymizationFailedError as exc:
            logger.exception(f"Anonymization failed for study {study_uid}")
            if record_id is not None:
                await self._submit(
                    record_id,
                    {"error": str(exc), **(extra_record_data or {})},
                    status=RecordStatus.failed,
                )
            raise

        if record_id is not None:
            await self._submit(
                record_id,
                self._record_data_from_result(result, extra_record_data),
            )

        return result

    async def _already_done(
        self,
        study: StudyRead,
        record_id: int,
        do_send: bool,
    ) -> str | None:
        """Return existing anon_study_uid when anonymization is already complete.

        Re-running is allowed when the previous attempt errored, or when this
        run sends to PACS but the previous one did not.
        """
        if study.anon_uid is None:
            return None
        record = await self.client.get_record(record_id)
        prev_data = record.data or {}
        if "error" in prev_data:
            return None
        if do_send and not prev_data.get("sent_to_pacs", False):
            return None
        return study.anon_uid

    async def _ensure_patient_anonymized(self, patient_id: str) -> None:
        """Assign Patient.anon_name when missing.

        Why ignore 409: concurrent workers may both reach this step for the same
        patient; the second one gets 409 (AlreadyAnonymizedError) which is benign
        and must not abort the rest of the flow.
        """
        try:
            await self.client.anonymize_patient(patient_id)
        except ClarinetAPIError as exc:
            if exc.status_code != 409:
                raise
            logger.debug(f"Patient {patient_id} already anonymized")

    @staticmethod
    def _record_data_from_result(
        result: AnonymizationResult,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "anon_study_uid": result.anon_study_uid,
            "instances_anonymized": result.instances_anonymized,
            "instances_failed": result.instances_failed,
            "instances_send_failed": result.instances_send_failed,
            "sent_to_pacs": result.sent_to_pacs,
            "series_count": result.series_count,
            "series_anonymized": result.series_anonymized,
            "series_skipped": result.series_skipped,
        }
        if extra:
            data.update(extra)
        return data

    async def _submit(
        self,
        record_id: int,
        data: dict[str, Any],
        *,
        status: RecordStatus | None = None,
    ) -> None:
        """Submit data to the Record.

        Uses PATCH (``update_record_data``) when the Record is already finished
        and no explicit status is provided; otherwise POST (``submit_record_data``).
        Without this guard, a re-run on a finished Record would 409 on POST.
        """
        record = await self.client.get_record(record_id)
        if record.status == RecordStatus.finished and status is None:
            await self.client.update_record_data(record_id, data)
        else:
            await self.client.submit_record_data(record_id, data, status=status)


@asynccontextmanager
async def create_anonymization_orchestrator(
    client: ClarinetClient | None = None,
) -> AsyncGenerator[AnonymizationOrchestrator]:
    """Create a Record-aware anonymization orchestrator.

    When ``client`` is provided (typically ``ctx.client`` from a pipeline task),
    it is reused without lifecycle management. Otherwise a fresh
    ``ClarinetClient`` is created from settings and closed on exit.
    """
    if client is not None:
        yield _build_orchestrator(client)
        return

    async with ClarinetClient(
        base_url=settings.effective_api_base_url,
        service_token=settings.effective_service_token,
        verify_ssl=settings.api_verify_ssl,
    ) as own_client:
        yield _build_orchestrator(own_client)


def build_anonymization_service(client: ClarinetClient) -> AnonymizationService:
    """Build an HTTP-backed ``AnonymizationService`` from a ``ClarinetClient``.

    Single point of configuration for the worker/orchestrator path; reused by
    ``create_anonymization_service`` (raw) and ``_build_orchestrator``.
    """
    from clarinet.services.dicom.client import DicomClient
    from clarinet.services.dicom.models import DicomNode
    from clarinet.services.dicom.repo_adapters import (
        PatientRepoAdapter,
        SeriesRepoAdapter,
        StudyRepoAdapter,
    )

    dicom_client = DicomClient(
        calling_aet=settings.dicom_aet,
        max_pdu=settings.dicom_max_pdu,
    )
    pacs = DicomNode(
        aet=settings.pacs_aet,
        host=settings.pacs_host,
        port=settings.pacs_port,
    )
    return AnonymizationService(
        study_repo=StudyRepoAdapter(client),  # type: ignore[arg-type]
        patient_repo=PatientRepoAdapter(client),  # type: ignore[arg-type]
        series_repo=SeriesRepoAdapter(client),  # type: ignore[arg-type]
        dicom_client=dicom_client,
        pacs=pacs,
    )


def _build_orchestrator(client: ClarinetClient) -> AnonymizationOrchestrator:
    return AnonymizationOrchestrator(build_anonymization_service(client), client)
