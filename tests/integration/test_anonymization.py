"""Integration tests for the DICOM anonymization endpoint."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydicom import Dataset

from clarinet.services.dicom.models import (
    BackgroundAnonymizationStatus,
    BatchStoreResult,
    RetrieveResult,
)
from tests.utils.factories import make_patient


@pytest.fixture
def mock_anon_settings() -> Generator[tuple[MagicMock, MagicMock], None, None]:
    """Patch anonymization_service.settings and series_filter.settings.

    Provides sensible defaults for all settings accessed by AnonymizationService.
    Tests can override individual attributes on the returned mocks.
    """
    with (
        patch("clarinet.services.anonymization_service.settings") as anon_settings,
        patch("clarinet.services.dicom.series_filter.settings") as filter_settings,
    ):
        anon_settings.anon_uid_salt = "test-salt"
        anon_settings.anon_save_to_disk = False
        anon_settings.anon_send_to_pacs = False
        anon_settings.anon_per_study_patient_id = False
        anon_settings.anon_failure_threshold = 0.5
        anon_settings.dicom_cget_max_retries = 1
        anon_settings.dicom_cget_retry_backoff = 0.0

        filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        filter_settings.series_filter_min_instance_count = None
        filter_settings.series_filter_unknown_modality_policy = "include"

        yield anon_settings, filter_settings


@pytest.mark.asyncio
async def test_anonymize_study_requires_auth(unauthenticated_client) -> None:
    """Endpoint requires superuser authentication."""
    response = await unauthenticated_client.post("/api/dicom/studies/1.2.3/anonymize")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_anonymize_study_not_found(client) -> None:
    """Returns 404 for non-existent study."""
    response = await client.post("/api/dicom/studies/9.9.9.9.9/anonymize")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patient_without_auto_id_rejected_by_db(test_session) -> None:
    """DB rejects patient without auto_id (NOT NULL constraint).

    Patient.auto_id has nullable=False. All production code goes through
    PatientRepository.create() which auto-assigns auto_id. This test verifies
    the DB-level safety net prevents persisting a patient without auto_id.
    """
    from sqlalchemy.exc import IntegrityError

    from clarinet.models.patient import Patient

    patient = Patient(id="NO_ANON_PAT", name="No Anon")
    test_session.add(patient)
    with pytest.raises(IntegrityError):
        await test_session.flush()
    await test_session.rollback()


@pytest.mark.asyncio
async def test_anonymize_study_success(client, test_session, mock_anon_settings) -> None:
    """Full anonymization flow with mocked PACS retrieval."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    # Create patient with auto_id
    patient = make_patient("ANON_PAT_001", "Test Patient", auto_id=42)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="ANON_PAT_001",
        study_uid="1.2.888.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(
        study_uid="1.2.888.1",
        series_uid="1.2.888.1.1",
        series_number=1,
    )
    test_session.add(series)
    await test_session.commit()

    # Create mock DICOM datasets
    mock_ds = Dataset()
    mock_ds.PatientID = "ANON_PAT_001"
    mock_ds.PatientName = "Test Patient"
    mock_ds.StudyInstanceUID = "1.2.888.1"
    mock_ds.SeriesInstanceUID = "1.2.888.1.1"
    mock_ds.SOPInstanceUID = "1.2.888.1.1.1"
    mock_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    mock_ds.Modality = "CT"

    file_meta = Dataset()
    file_meta.MediaStorageSOPInstanceUID = "1.2.888.1.1.1"
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    mock_ds.file_meta = file_meta

    mock_retrieve_result = RetrieveResult(
        status="success",
        num_completed=1,
        instances={"1.2.888.1.1.1": mock_ds},
    )

    with patch(
        "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
        new_callable=AsyncMock,
        return_value=mock_retrieve_result,
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.888.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["study_uid"] == "1.2.888.1"
    assert data["anon_study_uid"].startswith("2.25.")
    assert data["instances_anonymized"] == 1
    assert data["instances_failed"] == 0
    assert data["series_count"] == 1
    assert data["series_anonymized"] == 1
    assert data["series_skipped"] == 0
    assert data["skipped_series"] == []


@pytest.mark.asyncio
async def test_anonymize_study_background(client, test_session) -> None:
    """Background mode with a tracking Record returns 202."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("BG_PAT_001", "BG Patient", auto_id=99)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="BG_PAT_001",
        study_uid="1.2.777.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(
        study_uid="1.2.777.1",
        series_uid="1.2.777.1.1",
        series_number=1,
    )
    test_session.add(series)
    await test_session.commit()

    await _seed_anonymize_study_record(test_session, "1.2.777.1", "BG_PAT_001")

    with patch(
        "clarinet.api.routers.dicom._dispatch_background_anonymization",
        new_callable=AsyncMock,
        return_value=BackgroundAnonymizationStatus(study_uid="1.2.777.1"),
    ):
        response = await client.post("/api/dicom/studies/1.2.777.1/anonymize?background=true")

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "started"
    assert data["study_uid"] == "1.2.777.1"


@pytest.mark.asyncio
async def test_anonymize_study_filters_sr_series(client, test_session, mock_anon_settings) -> None:
    """SR series is excluded from anonymization and reported in skipped_series."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("FILTER_PAT_001", "Filter Patient", auto_id=50)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="FILTER_PAT_001",
        study_uid="1.2.555.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    # CT series — should be anonymized
    ct_series = Series(
        study_uid="1.2.555.1",
        series_uid="1.2.555.1.1",
        series_number=1,
        modality="CT",
        instance_count=1,  # must match mock (1 instance returned)
    )
    # SR series — should be skipped
    sr_series = Series(
        study_uid="1.2.555.1",
        series_uid="1.2.555.1.2",
        series_number=2,
        modality="SR",
        instance_count=1,
        series_description="Dose Report",
    )
    test_session.add_all([ct_series, sr_series])
    await test_session.commit()

    # Mock PACS retrieval for the CT series only
    mock_ds = Dataset()
    mock_ds.PatientID = "FILTER_PAT_001"
    mock_ds.PatientName = "Filter Patient"
    mock_ds.StudyInstanceUID = "1.2.555.1"
    mock_ds.SeriesInstanceUID = "1.2.555.1.1"
    mock_ds.SOPInstanceUID = "1.2.555.1.1.1"
    mock_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    mock_ds.Modality = "CT"

    file_meta = Dataset()
    file_meta.MediaStorageSOPInstanceUID = "1.2.555.1.1.1"
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    mock_ds.file_meta = file_meta

    mock_retrieve_result = RetrieveResult(
        status="success",
        num_completed=1,
        instances={"1.2.555.1.1.1": mock_ds},
    )

    with patch(
        "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
        new_callable=AsyncMock,
        return_value=mock_retrieve_result,
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.555.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["series_count"] == 2
    assert data["series_anonymized"] == 1
    assert data["series_skipped"] == 1
    assert data["instances_anonymized"] == 1
    assert len(data["skipped_series"]) == 1
    skipped = data["skipped_series"][0]
    assert skipped["series_uid"] == "1.2.555.1.2"
    assert skipped["modality"] == "SR"
    assert skipped["series_description"] == "Dose Report"
    assert "SR" in skipped["reason"]


@pytest.mark.asyncio
async def test_anonymize_pacs_retrieval_failure(client, test_session, mock_anon_settings) -> None:
    """PACS retrieval failure on one series does not block other series."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("PACS_FAIL_PAT", "PacsFail Patient", auto_id=60)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="PACS_FAIL_PAT",
        study_uid="1.2.600.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    s1 = Series(study_uid="1.2.600.1", series_uid="1.2.600.1.1", series_number=1, modality="CT")
    s2 = Series(study_uid="1.2.600.1", series_uid="1.2.600.1.2", series_number=2, modality="CT")
    test_session.add_all([s1, s2])
    await test_session.commit()

    # Mock dataset for second series
    mock_ds = Dataset()
    mock_ds.PatientID = "PACS_FAIL_PAT"
    mock_ds.PatientName = "PacsFail Patient"
    mock_ds.StudyInstanceUID = "1.2.600.1"
    mock_ds.SeriesInstanceUID = "1.2.600.1.2"
    mock_ds.SOPInstanceUID = "1.2.600.1.2.1"
    mock_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    mock_ds.Modality = "CT"

    file_meta = Dataset()
    file_meta.MediaStorageSOPInstanceUID = "1.2.600.1.2.1"
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    mock_ds.file_meta = file_meta

    success_result = RetrieveResult(
        status="success", num_completed=1, instances={"1.2.600.1.2.1": mock_ds}
    )

    call_count = 0

    async def _get_series_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if kwargs.get("series_uid") == "1.2.600.1.1":
            raise Exception("PACS connection timeout")
        return success_result

    anon_settings, _ = mock_anon_settings
    anon_settings.anon_failure_threshold = 1.0  # Allow partial failure for resilience test

    with patch(
        "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
        new_callable=AsyncMock,
        side_effect=_get_series_side_effect,
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.600.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["instances_failed"] == 1
    assert data["instances_anonymized"] == 1


@pytest.mark.asyncio
async def test_anonymize_instance_failure(client, test_session, mock_anon_settings) -> None:
    """anonymize_dataset failure on one instance does not block others."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("INST_FAIL_PAT", "InstFail Patient", auto_id=61)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="INST_FAIL_PAT",
        study_uid="1.2.601.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(study_uid="1.2.601.1", series_uid="1.2.601.1.1", series_number=1, modality="CT")
    test_session.add(series)
    await test_session.commit()

    # Create 3 mock datasets
    instances = {}
    for i in range(1, 4):
        ds = Dataset()
        ds.PatientID = "INST_FAIL_PAT"
        ds.PatientName = "InstFail Patient"
        ds.StudyInstanceUID = "1.2.601.1"
        ds.SeriesInstanceUID = "1.2.601.1.1"
        ds.SOPInstanceUID = f"1.2.601.1.1.{i}"
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        ds.Modality = "CT"

        file_meta = Dataset()
        file_meta.MediaStorageSOPInstanceUID = f"1.2.601.1.1.{i}"
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
        ds.file_meta = file_meta
        instances[f"1.2.601.1.1.{i}"] = ds

    mock_retrieve_result = RetrieveResult(status="success", num_completed=3, instances=instances)

    call_count = 0

    def _anonymize_side_effect(dataset):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("Anonymization error on instance 2")
        # Call real anonymizer for other instances
        from dicomanonymizer import simpledicomanonymizer

        simpledicomanonymizer.dictionary.clear()
        simpledicomanonymizer.anonymize_dataset(dataset, delete_private_tags=True)

    with (
        patch(
            "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
            new_callable=AsyncMock,
            return_value=mock_retrieve_result,
        ),
        patch(
            "clarinet.services.dicom.anonymizer.DicomAnonymizer.anonymize_dataset",
            side_effect=_anonymize_side_effect,
        ),
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.601.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["instances_failed"] == 1
    assert data["instances_anonymized"] == 2


@pytest.mark.asyncio
async def test_anonymize_send_to_pacs_failure_resilient(
    client, test_session, mock_anon_settings
) -> None:
    """C-STORE failure in _send_to_pacs does not crash the workflow."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("SEND_FAIL_PAT", "SendFail Patient", auto_id=62)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="SEND_FAIL_PAT",
        study_uid="1.2.602.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(study_uid="1.2.602.1", series_uid="1.2.602.1.1", series_number=1, modality="CT")
    test_session.add(series)
    await test_session.commit()

    mock_ds = Dataset()
    mock_ds.PatientID = "SEND_FAIL_PAT"
    mock_ds.PatientName = "SendFail Patient"
    mock_ds.StudyInstanceUID = "1.2.602.1"
    mock_ds.SeriesInstanceUID = "1.2.602.1.1"
    mock_ds.SOPInstanceUID = "1.2.602.1.1.1"
    mock_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    mock_ds.Modality = "CT"

    file_meta = Dataset()
    file_meta.MediaStorageSOPInstanceUID = "1.2.602.1.1.1"
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    mock_ds.file_meta = file_meta

    mock_retrieve_result = RetrieveResult(
        status="success", num_completed=1, instances={"1.2.602.1.1.1": mock_ds}
    )

    anon_settings, _ = mock_anon_settings
    anon_settings.anon_send_to_pacs = True

    with (
        patch(
            "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
            new_callable=AsyncMock,
            return_value=mock_retrieve_result,
        ),
        patch(
            "clarinet.services.dicom.client.DicomClient.store_instances_batch",
            new_callable=AsyncMock,
            side_effect=Exception("C-STORE failed"),
        ),
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.602.1/anonymize",
            json={"send_to_pacs": True, "save_to_disk": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["instances_anonymized"] == 1  # anonymization succeeded
    assert data["instances_failed"] == 0  # no anonymization failures
    assert data["instances_send_failed"] == 1  # C-STORE failed


@pytest.mark.asyncio
async def test_anonymize_save_to_disk_error_graceful(
    client, test_session, mock_anon_settings
) -> None:
    """Disk save error is caught by gather(return_exceptions=True) — response is still 200."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("DISK_FAIL_PAT", "DiskFail Patient", auto_id=63)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="DISK_FAIL_PAT",
        study_uid="1.2.603.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(study_uid="1.2.603.1", series_uid="1.2.603.1.1", series_number=1, modality="CT")
    test_session.add(series)
    await test_session.commit()

    mock_ds = Dataset()
    mock_ds.PatientID = "DISK_FAIL_PAT"
    mock_ds.PatientName = "DiskFail Patient"
    mock_ds.StudyInstanceUID = "1.2.603.1"
    mock_ds.SeriesInstanceUID = "1.2.603.1.1"
    mock_ds.SOPInstanceUID = "1.2.603.1.1.1"
    mock_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    mock_ds.Modality = "CT"

    file_meta = Dataset()
    file_meta.MediaStorageSOPInstanceUID = "1.2.603.1.1.1"
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    mock_ds.file_meta = file_meta

    mock_retrieve_result = RetrieveResult(
        status="success", num_completed=1, instances={"1.2.603.1.1.1": mock_ds}
    )

    anon_settings, _ = mock_anon_settings
    anon_settings.anon_save_to_disk = True
    anon_settings.storage_path = "/tmp/test_anon"

    with (
        patch(
            "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
            new_callable=AsyncMock,
            return_value=mock_retrieve_result,
        ),
        patch(
            "clarinet.services.anonymization_service.AnonymizationService._save_series_to_disk",
            new_callable=AsyncMock,
            side_effect=OSError("Disk write failed"),
        ),
    ):
        # Disk error is caught by gather(return_exceptions=True) — does not crash
        response = await client.post(
            "/api/dicom/studies/1.2.603.1/anonymize",
            json={"save_to_disk": True, "send_to_pacs": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["instances_anonymized"] == 1


@pytest.mark.asyncio
async def test_anonymize_study_no_series(client, test_session, mock_anon_settings) -> None:
    """Study with no series returns valid result with zeros."""
    from datetime import UTC, datetime

    from clarinet.models.study import Study

    patient = make_patient("NOSERIES_PAT", "NoSeries Patient", auto_id=64)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="NOSERIES_PAT",
        study_uid="1.2.604.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    response = await client.post(
        "/api/dicom/studies/1.2.604.1/anonymize",
        json={"save_to_disk": False, "send_to_pacs": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["series_count"] == 0
    assert data["instances_anonymized"] == 0
    assert data["instances_failed"] == 0


@pytest.mark.asyncio
async def test_anonymize_all_series_filtered(client, test_session, mock_anon_settings) -> None:
    """All series filtered out (SR/KO) results in zero anonymized."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("ALLFILT_PAT", "AllFilt Patient", auto_id=65)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="ALLFILT_PAT",
        study_uid="1.2.605.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    sr_series = Series(
        study_uid="1.2.605.1",
        series_uid="1.2.605.1.1",
        series_number=1,
        modality="SR",
        instance_count=1,
    )
    ko_series = Series(
        study_uid="1.2.605.1",
        series_uid="1.2.605.1.2",
        series_number=2,
        modality="KO",
        instance_count=1,
    )
    test_session.add_all([sr_series, ko_series])
    await test_session.commit()

    response = await client.post(
        "/api/dicom/studies/1.2.605.1/anonymize",
        json={"save_to_disk": False, "send_to_pacs": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["series_count"] == 2
    assert data["series_skipped"] == 2
    assert data["series_anonymized"] == 0
    assert data["instances_anonymized"] == 0


@pytest.mark.asyncio
async def test_anonymize_patient_not_found(client, test_session, mock_anon_settings) -> None:
    """PatientNotFoundError during anonymization → 404.

    FK constraints prevent orphan studies, so we create valid data and mock
    the patient lookup to raise PatientNotFoundError.
    """
    from datetime import UTC, datetime

    from clarinet.exceptions.domain import PatientNotFoundError
    from clarinet.models.study import Study
    from clarinet.repositories.patient_repository import PatientRepository

    patient = make_patient("GHOST_PAT", "Ghost")
    study = Study(
        patient_id="GHOST_PAT",
        study_uid="1.2.606.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add_all([patient, study])
    await test_session.commit()

    with patch.object(PatientRepository, "get", side_effect=PatientNotFoundError("GHOST_PAT")):
        response = await client.post(
            "/api/dicom/studies/1.2.606.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_anonymize_batch_cstore_partial_failure(
    client, test_session, mock_anon_settings
) -> None:
    """Batch C-STORE with partial failure reports correct send_failed count."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("BATCH_FAIL_PAT", "BatchFail Patient", auto_id=66)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="BATCH_FAIL_PAT",
        study_uid="1.2.607.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(study_uid="1.2.607.1", series_uid="1.2.607.1.1", series_number=1, modality="CT")
    test_session.add(series)
    await test_session.commit()

    # Create 3 mock datasets
    instances = {}
    for i in range(1, 4):
        ds = Dataset()
        ds.PatientID = "BATCH_FAIL_PAT"
        ds.PatientName = "BatchFail Patient"
        ds.StudyInstanceUID = "1.2.607.1"
        ds.SeriesInstanceUID = "1.2.607.1.1"
        ds.SOPInstanceUID = f"1.2.607.1.1.{i}"
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        ds.Modality = "CT"

        file_meta = Dataset()
        file_meta.MediaStorageSOPInstanceUID = f"1.2.607.1.1.{i}"
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
        ds.file_meta = file_meta
        instances[f"1.2.607.1.1.{i}"] = ds

    mock_retrieve_result = RetrieveResult(status="success", num_completed=3, instances=instances)

    # Batch C-STORE returns partial failure: 2 sent, 1 failed
    mock_batch_result = BatchStoreResult(
        total_sent=2, total_failed=1, failed_sop_uids=["1.2.607.1.1.2"]
    )

    anon_settings, _ = mock_anon_settings
    anon_settings.anon_send_to_pacs = True

    with (
        patch(
            "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
            new_callable=AsyncMock,
            return_value=mock_retrieve_result,
        ),
        patch(
            "clarinet.services.dicom.client.DicomClient.store_instances_batch",
            new_callable=AsyncMock,
            return_value=mock_batch_result,
        ),
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.607.1/anonymize",
            json={"send_to_pacs": True, "save_to_disk": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["instances_anonymized"] == 3
    assert data["instances_failed"] == 0
    assert data["instances_send_failed"] == 1


# ---------------------------------------------------------------------------
# Record-aware path: when an `anonymize-study` Record exists for the study,
# the endpoint dispatches via AnonymizationOrchestrator instead of raw
# AnonymizationService.
# ---------------------------------------------------------------------------


async def _seed_anonymize_study_record(
    test_session,
    study_uid: str,
    patient_id: str,
) -> int:
    """Create RecordType + Record for ``anonymize-study`` and return record id."""
    from clarinet.models.base import DicomQueryLevel
    from clarinet.models.record import Record, RecordType

    if await test_session.get(RecordType, "anonymize-study") is None:
        rt = RecordType(name="anonymize-study", level=DicomQueryLevel.STUDY)
        test_session.add(rt)
        await test_session.commit()

    record = Record(
        patient_id=patient_id,
        study_uid=study_uid,
        record_type_name="anonymize-study",
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)
    return record.id


@pytest.mark.asyncio
async def test_anonymize_with_record_dispatches_orchestrator(
    client, test_session, mock_anon_settings
) -> None:
    """When a tracking Record exists, sync mode goes through the Orchestrator."""
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime

    from clarinet.models.study import Study
    from clarinet.services.dicom.models import AnonymizationResult

    patient = make_patient("ORCH_PAT_001", "Orch Patient", auto_id=70)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="ORCH_PAT_001",
        study_uid="1.2.700.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    record_id = await _seed_anonymize_study_record(test_session, "1.2.700.1", "ORCH_PAT_001")

    expected = AnonymizationResult(
        study_uid="1.2.700.1",
        anon_study_uid="2.25.42",
        series_count=0,
        series_anonymized=0,
        series_skipped=0,
        instances_anonymized=0,
        instances_failed=0,
    )

    orch = AsyncMock()
    orch.run = AsyncMock(return_value=expected)

    @asynccontextmanager
    async def fake_factory(client=None):
        yield orch

    with patch(
        "clarinet.api.routers.dicom.create_anonymization_orchestrator",
        fake_factory,
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.700.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 200
    assert response.json()["anon_study_uid"] == "2.25.42"
    orch.run.assert_awaited_once()
    assert orch.run.await_args.kwargs["record_id"] == record_id


@pytest.mark.asyncio
async def test_anonymize_background_no_record_returns_404(client, test_session) -> None:
    """Background dispatch without a tracking Record returns 404."""
    from datetime import UTC, datetime

    from clarinet.models.study import Study

    patient = make_patient("NOREC_PAT", "NoRec Patient", auto_id=71)
    study = Study(
        patient_id="NOREC_PAT",
        study_uid="1.2.701.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add_all([patient, study])
    await test_session.commit()

    response = await client.post("/api/dicom/studies/1.2.701.1/anonymize?background=true")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_anonymize_background_with_record_dispatches(client, test_session) -> None:
    """Background dispatch with a tracking Record returns 202 and calls dispatcher."""
    from datetime import UTC, datetime

    from clarinet.models.study import Study
    from clarinet.services.dicom.models import BackgroundAnonymizationStatus

    patient = make_patient("BGREC_PAT", "BgRec Patient", auto_id=72)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="BGREC_PAT",
        study_uid="1.2.702.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    record_id = await _seed_anonymize_study_record(test_session, "1.2.702.1", "BGREC_PAT")

    with patch(
        "clarinet.api.routers.dicom._dispatch_background_anonymization",
        new_callable=AsyncMock,
        return_value=BackgroundAnonymizationStatus(study_uid="1.2.702.1"),
    ) as mock_dispatch:
        response = await client.post("/api/dicom/studies/1.2.702.1/anonymize?background=true")

    assert response.status_code == 202
    mock_dispatch.assert_awaited_once()
    dispatched_record = mock_dispatch.await_args.args[1]
    assert dispatched_record.id == record_id


@pytest.mark.asyncio
async def test_anonymize_no_record_sync_falls_back_to_raw(
    client, test_session, mock_anon_settings
) -> None:
    """Sync without Record runs raw anonymization (backwards compatible)."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("RAW_PAT_001", "Raw Patient", auto_id=73)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="RAW_PAT_001",
        study_uid="1.2.703.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(study_uid="1.2.703.1", series_uid="1.2.703.1.1", series_number=1)
    test_session.add(series)
    await test_session.commit()

    mock_ds = Dataset()
    mock_ds.PatientID = "RAW_PAT_001"
    mock_ds.PatientName = "Raw Patient"
    mock_ds.StudyInstanceUID = "1.2.703.1"
    mock_ds.SeriesInstanceUID = "1.2.703.1.1"
    mock_ds.SOPInstanceUID = "1.2.703.1.1.1"
    mock_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    mock_ds.Modality = "CT"

    file_meta = Dataset()
    file_meta.MediaStorageSOPInstanceUID = "1.2.703.1.1.1"
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    mock_ds.file_meta = file_meta

    mock_retrieve_result = RetrieveResult(
        status="success", num_completed=1, instances={"1.2.703.1.1.1": mock_ds}
    )

    with patch(
        "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
        new_callable=AsyncMock,
        return_value=mock_retrieve_result,
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.703.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_anonymize_per_study_mode_writes_hash_into_dicom(
    client, test_session, mock_anon_settings
) -> None:
    """``per_study_patient_id=True`` writes 8-hex hash into PatientID/PatientName."""
    import hashlib
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("PERSTUDY_PAT_001", "Per Study Patient", auto_id=701)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="PERSTUDY_PAT_001",
        study_uid="1.2.4242.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(study_uid="1.2.4242.1", series_uid="1.2.4242.1.1", series_number=1)
    test_session.add(series)
    await test_session.commit()

    mock_ds = Dataset()
    mock_ds.PatientID = "PERSTUDY_PAT_001"
    mock_ds.PatientName = "Per Study Patient"
    mock_ds.StudyInstanceUID = "1.2.4242.1"
    mock_ds.SeriesInstanceUID = "1.2.4242.1.1"
    mock_ds.SOPInstanceUID = "1.2.4242.1.1.1"
    mock_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    mock_ds.Modality = "CT"

    file_meta = Dataset()
    file_meta.MediaStorageSOPInstanceUID = "1.2.4242.1.1.1"
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    mock_ds.file_meta = file_meta

    mock_retrieve_result = RetrieveResult(
        status="success", num_completed=1, instances={"1.2.4242.1.1.1": mock_ds}
    )

    with patch(
        "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
        new_callable=AsyncMock,
        return_value=mock_retrieve_result,
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.4242.1/anonymize",
            json={
                "save_to_disk": False,
                "send_to_pacs": False,
                "per_study_patient_id": True,
            },
        )

    assert response.status_code == 200
    data = response.json()

    expected_hash = hashlib.sha256(b"test-salt:1.2.4242.1").hexdigest()[:8]
    assert data["anon_patient_id"] == expected_hash
    assert len(data["anon_patient_id"]) == 8

    # Hash, not the patient-level "CLARINET_701"
    assert "CLARINET" not in data["anon_patient_id"]

    # The DICOM dataset was mutated in-place by the anonymizer
    assert mock_ds.PatientID == expected_hash
    assert str(mock_ds.PatientName) == expected_hash


@pytest.mark.asyncio
async def test_anonymize_default_mode_returns_patient_anon_id(
    client, test_session, mock_anon_settings
) -> None:
    """Default mode (per_study_patient_id=False) keeps patient-level anon_id."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study

    patient = make_patient("DEFAULT_PAT_001", "Default Patient", auto_id=702)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="DEFAULT_PAT_001",
        study_uid="1.2.4243.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(study_uid="1.2.4243.1", series_uid="1.2.4243.1.1", series_number=1)
    test_session.add(series)
    await test_session.commit()

    mock_ds = Dataset()
    mock_ds.PatientID = "DEFAULT_PAT_001"
    mock_ds.PatientName = "Default Patient"
    mock_ds.StudyInstanceUID = "1.2.4243.1"
    mock_ds.SeriesInstanceUID = "1.2.4243.1.1"
    mock_ds.SOPInstanceUID = "1.2.4243.1.1.1"
    mock_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    mock_ds.Modality = "CT"

    file_meta = Dataset()
    file_meta.MediaStorageSOPInstanceUID = "1.2.4243.1.1.1"
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    mock_ds.file_meta = file_meta

    mock_retrieve_result = RetrieveResult(
        status="success", num_completed=1, instances={"1.2.4243.1.1.1": mock_ds}
    )

    with patch(
        "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
        new_callable=AsyncMock,
        return_value=mock_retrieve_result,
    ):
        response = await client.post(
            "/api/dicom/studies/1.2.4243.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 200
    data = response.json()
    # patient.anon_id == f"{settings.anon_id_prefix}_{auto_id}"
    assert data["anon_patient_id"] == patient.anon_id
    assert data["anon_patient_id"] is not None and "_" in data["anon_patient_id"]
