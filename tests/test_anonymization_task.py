"""Unit tests for src.services.dicom.tasks — background anonymization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.dicom.models import BackgroundAnonymizationStatus


@pytest.mark.asyncio
async def test_create_anonymization_service_yields_service() -> None:
    """_create_anonymization_service yields a properly constructed AnonymizationService."""
    mock_session = AsyncMock()

    with (
        patch("src.utils.db_manager.db_manager") as mock_db,
        patch("src.settings.settings") as mock_settings,
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

        from src.services.dicom.tasks import _create_anonymization_service

        async with _create_anonymization_service() as service:
            from src.services.anonymization_service import AnonymizationService

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
        "src.services.dicom.tasks._create_anonymization_service",
    ) as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_service)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.services.dicom.tasks import anonymize_study_background

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
        "src.services.dicom.tasks._create_anonymization_service",
    ) as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_service)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.services.dicom.tasks import anonymize_study_background

        # Should not raise
        await anonymize_study_background("1.2.3")

    mock_service.anonymize_study.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_background_pipeline_enabled() -> None:
    """When pipeline is enabled, dispatches via task.kiq()."""
    mock_task = MagicMock()
    mock_task.kiq = AsyncMock()

    with (
        patch("src.api.routers.dicom.settings") as mock_settings,
        patch(
            "src.services.dicom.tasks.get_anonymize_study_task",
            return_value=mock_task,
        ),
    ):
        mock_settings.pipeline_enabled = True

        from src.api.routers.dicom import _dispatch_background_anonymization

        result = await _dispatch_background_anonymization("1.2.3", True, False)

    assert result == BackgroundAnonymizationStatus(study_uid="1.2.3")
    mock_task.kiq.assert_awaited_once_with("1.2.3", save_to_disk=True, send_to_pacs=False)


@pytest.mark.asyncio
async def test_dispatch_background_pipeline_disabled() -> None:
    """When pipeline is disabled, dispatches via asyncio.create_task."""
    with (
        patch("src.api.routers.dicom.settings") as mock_settings,
        patch("src.api.routers.dicom.asyncio") as mock_asyncio,
        patch(
            "src.services.dicom.tasks.anonymize_study_background",
        ) as mock_bg,
    ):
        mock_settings.pipeline_enabled = False
        mock_bg.return_value = AsyncMock()()

        from src.api.routers.dicom import _dispatch_background_anonymization

        result = await _dispatch_background_anonymization("1.2.3", None, None)

    assert result == BackgroundAnonymizationStatus(study_uid="1.2.3")
    mock_asyncio.create_task.assert_called_once()
