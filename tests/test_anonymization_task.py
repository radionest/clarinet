"""Unit tests for clarinet.services.dicom.tasks + dispatch helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydicom import Dataset

from clarinet.exceptions.domain import AnonymizationFailedError
from clarinet.services.anonymization_service import AnonymizationService
from clarinet.services.dicom.models import BackgroundAnonymizationStatus


@pytest.mark.asyncio
async def test_create_anonymization_service_yields_service() -> None:
    """create_anonymization_service yields a service with HTTP-backed repos."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("clarinet.settings.settings") as mock_settings:
        mock_settings.effective_api_base_url = "http://test:8000/api"
        mock_settings.effective_service_token = "test-token"
        mock_settings.api_verify_ssl = False
        mock_settings.dicom_aet = "TEST_AET"
        mock_settings.dicom_max_pdu = 16384
        mock_settings.pacs_aet = "PACS_AET"
        mock_settings.pacs_host = "localhost"
        mock_settings.pacs_port = 11112

        with patch(
            "clarinet.client.ClarinetClient",
            return_value=mock_client,
        ) as mock_client_cls:
            from clarinet.services.dicom.tasks import create_anonymization_service

            async with create_anonymization_service() as service:
                from clarinet.services.dicom.repo_adapters import (
                    PatientRepoAdapter,
                    SeriesRepoAdapter,
                    StudyRepoAdapter,
                )

                mock_client_cls.assert_called_once_with(
                    base_url="http://test:8000/api",
                    service_token="test-token",
                    verify_ssl=False,
                )

                assert isinstance(service, AnonymizationService)
                assert isinstance(service.study_repo, StudyRepoAdapter)
                assert isinstance(service.patient_repo, PatientRepoAdapter)
                assert isinstance(service.series_repo, SeriesRepoAdapter)

                assert service.study_repo._client is mock_client
                assert service.patient_repo._client is mock_client
                assert service.series_repo._client is mock_client


@pytest.mark.asyncio
async def test_dispatch_background_pipeline_enabled() -> None:
    """When pipeline is enabled, dispatches via task.kicker().kiq()."""
    record = MagicMock()
    record.id = 42
    record.patient_id = "P1"

    mock_task = MagicMock()
    mock_task.kicker.return_value.kiq = AsyncMock()

    with (
        patch("clarinet.api.routers.dicom.settings") as mock_settings,
        patch("clarinet.services.dicom.pipeline.anonymize_study_pipeline", mock_task),
    ):
        mock_settings.pipeline_enabled = True

        from clarinet.api.routers.dicom import _dispatch_background_anonymization

        result = await _dispatch_background_anonymization("1.2.3", record, True, False, None)

    assert result == BackgroundAnonymizationStatus(study_uid="1.2.3")
    mock_task.kicker.return_value.kiq.assert_awaited_once()
    sent_msg = mock_task.kicker.return_value.kiq.await_args.args[0]
    assert sent_msg["study_uid"] == "1.2.3"
    assert sent_msg["record_id"] == 42
    assert sent_msg["patient_id"] == "P1"
    assert sent_msg["payload"]["save_to_disk"] is True
    assert sent_msg["payload"]["send_to_pacs"] is False
    assert "per_study_patient_id" not in sent_msg["payload"]


@pytest.mark.asyncio
async def test_dispatch_background_pipeline_disabled() -> None:
    """When pipeline is disabled, dispatches via asyncio.create_task."""
    record = MagicMock()
    record.id = 42
    record.patient_id = "P1"

    with (
        patch("clarinet.api.routers.dicom.settings") as mock_settings,
        patch("clarinet.api.routers.dicom.asyncio") as mock_asyncio,
    ):
        mock_settings.pipeline_enabled = False
        mock_asyncio.create_task = MagicMock()

        from clarinet.api.routers.dicom import _dispatch_background_anonymization

        result = await _dispatch_background_anonymization("1.2.3", record, None, None, None)

    assert result == BackgroundAnonymizationStatus(study_uid="1.2.3")
    mock_asyncio.create_task.assert_called_once()


@pytest.mark.asyncio
async def test_anonymize_study_raises_on_failure_threshold() -> None:
    """anonymize_study raises AnonymizationFailedError when failure ratio >= threshold."""
    mock_series = MagicMock()
    mock_series.series_uid = "1.2.3.4.5.6"
    mock_series.modality = "CT"
    mock_series.series_description = "Axial"

    mock_study = MagicMock()
    mock_study.patient_id = 1
    mock_study.series = [mock_series]

    mock_patient = MagicMock()
    mock_patient.anon_id = "ANON_001"
    mock_patient.anon_name = "AnonName"

    bad_ds = Dataset()

    mock_retrieve_result = MagicMock()
    mock_retrieve_result.instances = {"1.2.3.100": bad_ds}

    study_repo = AsyncMock()
    study_repo.get_with_series = AsyncMock(return_value=mock_study)
    study_repo.update_anon_uid = AsyncMock()

    patient_repo = AsyncMock()
    patient_repo.get = AsyncMock(return_value=mock_patient)

    series_repo = AsyncMock()
    series_repo.update_anon_uid = AsyncMock()

    dicom_client = AsyncMock()
    dicom_client.get_series_to_memory = AsyncMock(return_value=mock_retrieve_result)

    pacs = MagicMock()

    service = AnonymizationService(
        study_repo=study_repo,
        patient_repo=patient_repo,
        series_repo=series_repo,
        dicom_client=dicom_client,
        pacs=pacs,
    )

    with patch("clarinet.services.anonymization_service.settings") as mock_settings:
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = False
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_failure_threshold = 0.5
        mock_settings.series_filter_excluded_modalities = []
        mock_settings.series_filter_min_instance_count = 0
        mock_settings.series_filter_unknown_modality_policy = "include"

        with pytest.raises(AnonymizationFailedError, match="1/1 instances failed"):
            await service.anonymize_study("1.2.3.4.5")


@pytest.mark.asyncio
async def test_anonymize_study_succeeds_below_threshold() -> None:
    """anonymize_study completes when failure ratio is below threshold."""
    mock_series = MagicMock()
    mock_series.series_uid = "1.2.3.4.5.6"
    mock_series.modality = "CT"
    mock_series.series_description = "Axial"
    mock_series.instance_count = None

    mock_study = MagicMock()
    mock_study.patient_id = 1
    mock_study.series = [mock_series]

    mock_patient = MagicMock()
    mock_patient.anon_id = "ANON_001"
    mock_patient.anon_name = "AnonName"

    good_ds = Dataset()
    good_ds.PatientID = "REAL_PAT"
    good_ds.PatientName = "Real^Name"
    good_ds.StudyInstanceUID = "1.2.3.4.5"
    good_ds.SeriesInstanceUID = "1.2.3.4.5.6"
    good_ds.SOPInstanceUID = "1.2.3.100"
    good_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"

    mock_retrieve_result = MagicMock()
    mock_retrieve_result.instances = {"1.2.3.100": good_ds}

    study_repo = AsyncMock()
    study_repo.get_with_series = AsyncMock(return_value=mock_study)
    study_repo.update_anon_uid = AsyncMock()

    patient_repo = AsyncMock()
    patient_repo.get = AsyncMock(return_value=mock_patient)

    series_repo = AsyncMock()
    series_repo.update_anon_uid = AsyncMock()

    dicom_client = AsyncMock()
    dicom_client.get_series_to_memory = AsyncMock(return_value=mock_retrieve_result)

    pacs = MagicMock()

    service = AnonymizationService(
        study_repo=study_repo,
        patient_repo=patient_repo,
        series_repo=series_repo,
        dicom_client=dicom_client,
        pacs=pacs,
    )

    with patch("clarinet.services.anonymization_service.settings") as mock_settings:
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = False
        mock_settings.anon_per_study_patient_id = False
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_failure_threshold = 0.5
        mock_settings.dicom_cget_max_retries = 1
        mock_settings.series_filter_excluded_modalities = []
        mock_settings.series_filter_min_instance_count = 0
        mock_settings.series_filter_unknown_modality_policy = "include"
        mock_settings.storage_path = "/tmp/test"

        result = await service.anonymize_study("1.2.3.4.5")

    assert result.instances_anonymized == 1
    assert result.instances_failed == 0
