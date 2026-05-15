"""Anonymization service: fetch from PACS → anonymize in-memory → distribute."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydicom import Dataset

if TYPE_CHECKING:
    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study
    from clarinet.services.dicom.models import RetrieveResult

from clarinet.exceptions.domain import AnonymizationFailedError
from clarinet.models.base import DicomQueryLevel
from clarinet.repositories.patient_repository import PatientRepository
from clarinet.repositories.series_repository import SeriesRepository
from clarinet.repositories.study_repository import StudyRepository
from clarinet.services.common.storage_paths import build_context, render_working_folder
from clarinet.services.dicom.anonymizer import DicomAnonymizer, compute_per_study_patient_id
from clarinet.services.dicom.client import DicomClient
from clarinet.services.dicom.models import AnonymizationResult, DicomNode, SkippedSeriesInfo
from clarinet.services.dicom.series_filter import SeriesFilter, SeriesFilterCriteria
from clarinet.settings import settings
from clarinet.utils.logger import logger


@dataclass
class _DistributionResult:
    """Result of a background distribution task (disk save + C-STORE)."""

    send_failed: int = 0


class AnonymizationService:
    """Orchestrates DICOM anonymization: PACS → memory → distribute → DB.

    Args:
        study_repo: Study repository
        patient_repo: Patient repository
        series_repo: Series repository
        dicom_client: Async DICOM client
        pacs: PACS node configuration
    """

    def __init__(
        self,
        study_repo: StudyRepository,
        patient_repo: PatientRepository,
        series_repo: SeriesRepository,
        dicom_client: DicomClient,
        pacs: DicomNode,
    ):
        self.study_repo = study_repo
        self.patient_repo = patient_repo
        self.series_repo = series_repo
        self.dicom_client = dicom_client
        self.pacs = pacs

    async def _retrieve_series(
        self,
        study_uid: str,
        series: Series,
        max_retries: int | None = None,
    ) -> RetrieveResult | None:
        """Retrieve series from PACS with retry on incomplete results.

        Retries when PACS returns fewer instances than expected (transient
        failures under concurrent load). Uses exponential backoff.

        Args:
            study_uid: Study Instance UID
            series: Series to retrieve
            max_retries: Maximum number of attempts (default: settings.dicom_cget_max_retries)

        Returns:
            RetrieveResult with instances, or None if all attempts failed
        """
        retries = max_retries if max_retries is not None else settings.dicom_cget_max_retries
        expected = series.instance_count  # may be None

        for attempt in range(1, retries + 1):
            try:
                result = await self.dicom_client.get_series_to_memory(
                    study_uid=study_uid,
                    series_uid=series.series_uid,
                    peer=self.pacs,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    f"C-GET failed for series {series.series_uid} (attempt {attempt}/{retries})"
                )
                if attempt < retries:
                    await asyncio.sleep(settings.dicom_cget_retry_backoff**attempt)
                continue

            received = len(result.instances)

            # Validate: got instances and count matches expected (if known)
            if received > 0 and (expected is None or received >= expected):
                return result

            logger.warning(
                f"Incomplete C-GET for series {series.series_uid}: "
                f"got {received}/{expected or '?'} instances "
                f"(attempt {attempt}/{retries})"
            )
            if attempt < retries:
                await asyncio.sleep(settings.dicom_cget_retry_backoff**attempt)

        logger.error(f"All {retries} C-GET attempts failed for series {series.series_uid}")
        return None

    async def anonymize_study(
        self,
        study_uid: str,
        save_to_disk: bool | None = None,
        send_to_pacs: bool | None = None,
        per_study_patient_id: bool | None = None,
    ) -> AnonymizationResult:
        """Anonymize a study: fetch from PACS, anonymize, distribute.

        C-GET runs sequentially (one PACS association at a time).
        Distribution (disk save + batch C-STORE) runs as background tasks,
        overlapping with the next series' C-GET for pipeline parallelism.

        Args:
            study_uid: Study Instance UID
            save_to_disk: Override settings.anon_save_to_disk (None = use setting)
            send_to_pacs: Override settings.anon_send_to_pacs (None = use setting)
            per_study_patient_id: Override settings.anon_per_study_patient_id.
                When True, PatientID/PatientName are set to a per-study 8-hex
                hash (sha256(salt:study_uid)) — different across studies of the
                same patient, preventing PACS-side correlation.

        Returns:
            Anonymization result with statistics

        Raises:
            AnonymizationFailedError: If per-patient mode and patient has no anon_id
        """
        do_save = save_to_disk if save_to_disk is not None else settings.anon_save_to_disk
        do_send = send_to_pacs if send_to_pacs is not None else settings.anon_send_to_pacs
        do_per_study = (
            per_study_patient_id
            if per_study_patient_id is not None
            else settings.anon_per_study_patient_id
        )

        # Load study with series
        study = await self.study_repo.get_with_series(study_uid)
        patient = await self.patient_repo.get(study.patient_id)

        if do_per_study:
            anon_patient_id = compute_per_study_patient_id(
                settings.anon_uid_salt,
                study_uid,
                settings.anon_per_study_patient_id_hex_length,
                prefix=settings.anon_id_prefix,
            )
            anon_patient_name = anon_patient_id
        else:
            anon_id = patient.anon_id
            if anon_id is None:
                raise AnonymizationFailedError("Patient has no anon_id (auto_id not set)")
            anon_patient_id = anon_id
            anon_patient_name = patient.anon_name or anon_id

        anonymizer = DicomAnonymizer(
            salt=settings.anon_uid_salt,
            anon_patient_id=anon_patient_id,
            anon_patient_name=anon_patient_name,
        )

        anon_study_uid = anonymizer.generate_anon_uid(study_uid)
        series_list = list(study.series)
        series_count = len(series_list)
        total_anonymized = 0
        total_failed = 0
        total_send_failed = 0

        # Filter series
        series_filter = SeriesFilter()
        filter_result = series_filter.filter(
            series_list,
            to_criteria=SeriesFilterCriteria.from_series,
        )

        skipped_info = [
            SkippedSeriesInfo(
                series_uid=fi.item.series_uid,
                modality=fi.item.modality,
                series_description=fi.item.series_description,
                reason=fi.reason,
            )
            for fi in filter_result.excluded
        ]

        if filter_result.excluded:
            logger.info(
                f"Filtered out {len(filter_result.excluded)} series: "
                + ", ".join(f"{fi.item.series_uid} ({fi.reason})" for fi in filter_result.excluded)
            )

        logger.info(
            f"Anonymizing study {study_uid} → {anon_study_uid} "
            f"({len(filter_result.included)}/{series_count} series, "
            f"save_to_disk={do_save}, send_to_pacs={do_send})"
        )

        pending_tasks: list[asyncio.Task[_DistributionResult]] = []

        for series in filter_result.included:
            # 1. C-GET with retry — strictly sequential (one PACS association at a time)
            result = await self._retrieve_series(study_uid, series)
            if result is None:
                total_failed += 1
                continue

            # 2. Anonymize in-place (CPU, fast — stays in main loop)
            anon_series_uid = anonymizer.generate_anon_uid(series.series_uid)
            anonymized: list[Dataset] = []
            for sop_uid, ds in result.instances.items():
                try:
                    anonymizer.anonymize_dataset(ds)
                    anonymized.append(ds)
                except Exception:
                    logger.exception(f"Failed to anonymize instance {sop_uid}")
                    total_failed += 1

            # 3. Fire distribution as background task (overlaps with next C-GET)
            if anonymized and (do_save or do_send):
                task = asyncio.create_task(
                    self._distribute_series(
                        anonymized,
                        patient,
                        study,
                        series,
                        anon_patient_id,
                        anon_study_uid,
                        anon_series_uid,
                        do_save,
                        do_send,
                    )
                )
                pending_tasks.append(task)

            total_anonymized += len(anonymized)

            # 4. DB update (doesn't depend on distribution)
            await self.series_repo.update_anon_uid(series, anon_series_uid)

        # Await all distribution tasks
        if pending_tasks:
            dist_results = await asyncio.gather(*pending_tasks, return_exceptions=True)
            for r in dist_results:
                if isinstance(r, BaseException):
                    logger.error(f"Distribution task failed: {r}")
                else:
                    total_send_failed += r.send_failed

        # Check failure threshold
        total_instances = total_anonymized + total_failed
        if total_instances > 0:
            failure_ratio = total_failed / total_instances
            if failure_ratio >= settings.anon_failure_threshold:
                raise AnonymizationFailedError(
                    f"{total_failed}/{total_instances} instances failed "
                    f"(threshold: {settings.anon_failure_threshold:.0%})"
                )

        # Update study anon_uid in DB
        await self.study_repo.update_anon_uid(study, anon_study_uid)

        output_dir = str(Path(settings.storage_path)) if do_save else None

        logger.info(
            f"Anonymization complete: {total_anonymized} instances anonymized, "
            f"{total_failed} failed, {total_send_failed} send failed"
        )

        return AnonymizationResult(
            study_uid=study_uid,
            anon_study_uid=anon_study_uid,
            anon_patient_id=anon_patient_id,
            series_count=series_count,
            series_anonymized=len(filter_result.included),
            series_skipped=len(filter_result.excluded),
            instances_anonymized=total_anonymized,
            instances_failed=total_failed,
            instances_send_failed=total_send_failed,
            output_dir=output_dir,
            sent_to_pacs=do_send,
            skipped_series=skipped_info,
        )

    async def _distribute_series(
        self,
        datasets: list[Dataset],
        patient: Patient,
        study: Study,
        series: Series,
        anon_patient_id: str,
        anon_study_uid: str,
        anon_series_uid: str,
        do_save: bool,
        do_send: bool,
    ) -> _DistributionResult:
        """Distribute anonymized series: disk save and/or batch C-STORE in parallel.

        Args:
            datasets: Anonymized DICOM datasets for one series
            patient: Patient entity (for template placeholders)
            study: Study entity (for template placeholders)
            series: Series entity (for template placeholders)
            anon_patient_id: Anonymized patient ID
            anon_study_uid: Anonymized study UID
            anon_series_uid: Anonymized series UID
            do_save: Whether to save to disk
            do_send: Whether to send to PACS

        Returns:
            Distribution result with send failure count
        """
        tasks: list[asyncio.Task[int]] = []
        if do_save:
            tasks.append(
                asyncio.create_task(
                    self._save_series_to_disk(
                        datasets,
                        patient,
                        study,
                        series,
                        anon_patient_id,
                        anon_study_uid,
                        anon_series_uid,
                    )
                )
            )
        if do_send:
            tasks.append(asyncio.create_task(self._send_series_to_pacs(datasets)))

        result = _DistributionResult()
        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for tr in task_results:
                if isinstance(tr, int):
                    result.send_failed += tr
                elif isinstance(tr, BaseException):
                    logger.error(f"Distribution sub-task failed: {tr}")
        return result

    async def _save_series_to_disk(
        self,
        datasets: list[Dataset],
        patient: Patient,
        study: Study,
        series: Series,
        anon_patient_id: str,
        anon_study_uid: str,
        anon_series_uid: str,
    ) -> int:
        """Save all datasets for one series to disk.

        Path is resolved from ``settings.disk_path_template`` at SERIES
        level, then ``/dcm_anon`` is appended — so the anonymized output
        always lives directly under the series' working folder.

        Args:
            datasets: Anonymized DICOM datasets
            patient: Patient entity (template placeholders)
            study: Study entity (template placeholders)
            series: Series entity (template placeholders)
            anon_patient_id: Anonymized patient ID
            anon_study_uid: Anonymized study UID
            anon_series_uid: Anonymized series UID

        Returns:
            Always 0 (no send failures from disk save)
        """
        ctx = build_context(
            patient=patient,
            study=study,
            series=series,
            template=settings.disk_path_template,
            anon_patient_id=anon_patient_id,
            anon_study_uid=anon_study_uid,
            anon_series_uid=anon_series_uid,
        )
        series_dir = render_working_folder(
            settings.disk_path_template,
            DicomQueryLevel.SERIES,
            ctx,
            Path(settings.storage_path),
        )
        output_path = series_dir / "dcm_anon"
        await asyncio.to_thread(output_path.mkdir, parents=True, exist_ok=True)
        save_tasks = [
            asyncio.to_thread(
                self._write_dataset,
                ds,
                output_path / f"{getattr(ds, 'SOPInstanceUID', 'unknown')}.dcm",
            )
            for ds in datasets
        ]
        await asyncio.gather(*save_tasks)
        return 0

    async def _send_series_to_pacs(self, datasets: list[Dataset]) -> int:
        """Send all datasets for one series via batch C-STORE.

        Args:
            datasets: Anonymized DICOM datasets

        Returns:
            Number of failed sends
        """
        try:
            batch_result = await self.dicom_client.store_instances_batch(datasets, self.pacs)
            return batch_result.total_failed
        except Exception:
            logger.exception("Failed to batch C-STORE anonymized series to PACS")
            return len(datasets)

    @staticmethod
    def _write_dataset(dataset: Dataset, file_path: Path) -> None:
        """Write dataset to file (sync, called via to_thread)."""
        dataset.save_as(file_path, enforce_file_format=True)
