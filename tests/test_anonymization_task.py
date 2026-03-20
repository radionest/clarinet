"""Unit tests for clarinet.services.dicom.tasks — background anonymization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydicom import Dataset

from clarinet.exceptions.domain import AnonymizationFailedError
from clarinet.services.anonymization_service import AnonymizationService
from clarinet.services.dicom.models import BackgroundAnonymizationStatus


@pytest.mark.asyncio
async def test_create_anonymization_service_yields_service() -> None:
    """_create_anonymization_service yields a properly constructed AnonymizationService."""
    mock_session = AsyncMock()

    with (
        patch("clarinet.utils.db_manager.db_manager") as mock_db,
        patch("clarinet.settings.settings") as mock_settings,
    ):
        # Set up the async context manager to yield mock session
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_db.get_async_session_context.return_value = mock_ctx

        mock_settings.dicom_aet = "TEST_AET"
        mock_settings.dicom_max_pdu = 16384
        mock_settings.pacs_aet = "PACS_AET"
        mock_settings.pacs_host = "localhost"
        mock_settings.pacs_port = 11112

        from clarinet.services.dicom.tasks import _create_anonymization_service

        async with _create_anonymization_service() as service:
            from clarinet.services.anonymization_service import AnonymizationService

            assert isinstance(service, AnonymizationService)
            assert service.study_repo.session is mock_session
            assert service.patient_repo.session is mock_session
            assert service.series_repo.session is mock_session


@pytest.mark.asyncio
async def test_anonymize_study_background_success() -> None:
    """anonymize_study_background calls service.anonymize_study and logs success."""
    mock_service = AsyncMock()
    mock_service.anonymize_study = AsyncMock()

    with patch(
        "clarinet.services.dicom.tasks._create_anonymization_service",
    ) as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_service)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        from clarinet.services.dicom.tasks import anonymize_study_background

        await anonymize_study_background("1.2.3", save_to_disk=True, send_to_pacs=False)

    mock_service.anonymize_study.assert_awaited_once_with(
        "1.2.3", save_to_disk=True, send_to_pacs=False
    )


@pytest.mark.asyncio
async def test_anonymize_study_background_catches_exceptions() -> None:
    """anonymize_study_background catches and logs exceptions without raising."""
    mock_service = AsyncMock()
    mock_service.anonymize_study = AsyncMock(side_effect=RuntimeError("DB gone"))

    with patch(
        "clarinet.services.dicom.tasks._create_anonymization_service",
    ) as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_service)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        from clarinet.services.dicom.tasks import anonymize_study_background

        # Should not raise
        await anonymize_study_background("1.2.3")

    mock_service.anonymize_study.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_background_pipeline_enabled() -> None:
    """When pipeline is enabled, dispatches via task.kiq()."""
    mock_task = MagicMock()
    mock_task.kiq = AsyncMock()

    with (
        patch("clarinet.api.routers.dicom.settings") as mock_settings,
        patch(
            "clarinet.services.dicom.tasks.get_anonymize_study_task",
            return_value=mock_task,
        ),
    ):
        mock_settings.pipeline_enabled = True

        from clarinet.api.routers.dicom import _dispatch_background_anonymization

        result = await _dispatch_background_anonymization("1.2.3", True, False)

    assert result == BackgroundAnonymizationStatus(study_uid="1.2.3")
    mock_task.kiq.assert_awaited_once_with("1.2.3", save_to_disk=True, send_to_pacs=False)


@pytest.mark.asyncio
async def test_anonymize_study_raises_on_failure_threshold() -> None:
    """anonymize_study raises AnonymizationFailedError when failure ratio >= threshold."""
    # Create a mock study with one series
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

    # Build a dataset that will fail during anonymization
    bad_ds = Dataset()
    # Intentionally missing required tags → anonymizer will raise KeyError

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
    mock_series.instance_count = None  # unknown count — skip >= comparison

    mock_study = MagicMock()
    mock_study.patient_id = 1
    mock_study.series = [mock_series]

    mock_patient = MagicMock()
    mock_patient.anon_id = "ANON_001"
    mock_patient.anon_name = "AnonName"

    # Build a valid dataset
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


@pytest.mark.asyncio
async def test_dispatch_background_pipeline_disabled() -> None:
    """When pipeline is disabled, dispatches via asyncio.create_task."""
    with (
        patch("clarinet.api.routers.dicom.settings") as mock_settings,
        patch("clarinet.api.routers.dicom.asyncio") as mock_asyncio,
        patch(
            "clarinet.services.dicom.tasks.anonymize_study_background",
        ) as mock_bg,
    ):
        mock_settings.pipeline_enabled = False
        mock_bg.return_value = AsyncMock()()

        from clarinet.api.routers.dicom import _dispatch_background_anonymization

        result = await _dispatch_background_anonymization("1.2.3", None, None)

    assert result == BackgroundAnonymizationStatus(study_uid="1.2.3")
    mock_asyncio.create_task.assert_called_once()
