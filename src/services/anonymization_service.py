"""Anonymization service: fetch from PACS → anonymize in-memory → distribute."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from pydicom import Dataset

from src.exceptions.domain import AnonymizationFailedError
from src.repositories.patient_repository import PatientRepository
from src.repositories.series_repository import SeriesRepository
from src.repositories.study_repository import StudyRepository
from src.services.dicom.anonymizer import DicomAnonymizer
from src.services.dicom.client import DicomClient
from src.services.dicom.models import AnonymizationResult, DicomNode, SkippedSeriesInfo
from src.services.dicom.series_filter import SeriesFilter, SeriesFilterCriteria
from src.settings import settings
from src.utils.logger import logger


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

    async def anonymize_study(
        self,
        study_uid: str,
        save_to_disk: bool | None = None,
        send_to_pacs: bool | None = None,
    ) -> AnonymizationResult:
        """Anonymize a study: fetch from PACS, anonymize, distribute.

        C-GET runs sequentially (one PACS association at a time).
        Distribution (disk save + batch C-STORE) runs as background tasks,
        overlapping with the next series' C-GET for pipeline parallelism.

        Args:
            study_uid: Study Instance UID
            save_to_disk: Override settings.anon_save_to_disk (None = use setting)
            send_to_pacs: Override settings.anon_send_to_pacs (None = use setting)

        Returns:
            Anonymization result with statistics

        Raises:
            AnonymizationFailedError: If patient has no anon_id
        """
        do_save = save_to_disk if save_to_disk is not None else settings.anon_save_to_disk
        do_send = send_to_pacs if send_to_pacs is not None else settings.anon_send_to_pacs

        # Load study with series
        study = await self.study_repo.get_with_series(study_uid)
        patient = await self.patient_repo.get(study.patient_id)

        anon_id: str | None = patient.anon_id
        if anon_id is None:
            raise AnonymizationFailedError("Patient has no anon_id (auto_id not set)")

        anon_patient_name = patient.anon_name or anon_id
        anonymizer = DicomAnonymizer(
            salt=settings.anon_uid_salt,
            anon_patient_id=anon_id,
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
            # 1. C-GET — strictly sequential (one PACS association at a time)
            try:
                result = await self.dicom_client.get_series_to_memory(
                    study_uid=study_uid,
                    series_uid=series.series_uid,
                    peer=self.pacs,
                )
            except Exception:
                logger.exception(f"Failed to retrieve series {series.series_uid} from PACS")
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
                        anonymized, anon_id, anon_study_uid, anon_series_uid, do_save, do_send
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
        anon_patient_id: str,
        anon_study_uid: str,
        anon_series_uid: str,
        do_save: bool,
        do_send: bool,
    ) -> _DistributionResult:
        """Distribute anonymized series: disk save and/or batch C-STORE in parallel.

        Args:
            datasets: Anonymized DICOM datasets for one series
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
                        datasets, anon_patient_id, anon_study_uid, anon_series_uid
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
        anon_patient_id: str,
        anon_study_uid: str,
        anon_series_uid: str,
    ) -> int:
        """Save all datasets for one series to disk.

        Args:
            datasets: Anonymized DICOM datasets
            anon_patient_id: Anonymized patient ID
            anon_study_uid: Anonymized study UID
            anon_series_uid: Anonymized series UID

        Returns:
            Always 0 (no send failures from disk save)
        """
        output_path = (
            Path(settings.storage_path)
            / anon_patient_id
            / anon_study_uid
            / anon_series_uid
            / "dcm_anon"
        )
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
        dataset.save_as(file_path, write_like_original=False)
