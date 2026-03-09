"""Integration tests for the DICOM anonymization endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from pydicom import Dataset

from clarinet.services.dicom.models import (
    BackgroundAnonymizationStatus,
    BatchStoreResult,
    RetrieveResult,
)


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
async def test_anonymize_study_no_anon_id(client, test_session) -> None:
    """Returns 500 when patient has no anon_id (auto_id is None).

    Note: patient is created via session.add() (bypassing PatientRepository),
    so auto_id remains None — this tests the defensive path in anonymization.
    """
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    # Create patient without auto_id
    patient = Patient(id="NO_ANON_PAT", name="No Anon")
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="NO_ANON_PAT",
        study_uid="1.2.999.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(
        study_uid="1.2.999.1",
        series_uid="1.2.999.1.1",
        series_number=1,
    )
    test_session.add(series)
    await test_session.commit()

    response = await client.post("/api/dicom/studies/1.2.999.1/anonymize")
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_anonymize_study_success(client, test_session) -> None:
    """Full anonymization flow with mocked PACS retrieval."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    # Create patient with auto_id
    patient = Patient(id="ANON_PAT_001", name="Test Patient", auto_id=42)
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

    with (
        patch(
            "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
            new_callable=AsyncMock,
            return_value=mock_retrieve_result,
        ),
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = False
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

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
    """Background mode returns immediately with status."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    patient = Patient(id="BG_PAT_001", name="BG Patient", auto_id=99)
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
async def test_anonymize_study_filters_sr_series(client, test_session) -> None:
    """SR series is excluded from anonymization and reported in skipped_series."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    patient = Patient(id="FILTER_PAT_001", name="Filter Patient", auto_id=50)
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
        instance_count=120,
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

    with (
        patch(
            "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
            new_callable=AsyncMock,
            return_value=mock_retrieve_result,
        ),
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = False
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

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
async def test_anonymize_pacs_retrieval_failure(client, test_session) -> None:
    """PACS retrieval failure on one series does not block other series."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    patient = Patient(id="PACS_FAIL_PAT", name="PacsFail Patient", auto_id=60)
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

    with (
        patch(
            "clarinet.services.dicom.client.DicomClient.get_series_to_memory",
            new_callable=AsyncMock,
            side_effect=_get_series_side_effect,
        ),
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = False
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

        response = await client.post(
            "/api/dicom/studies/1.2.600.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["instances_failed"] == 1
    assert data["instances_anonymized"] == 1


@pytest.mark.asyncio
async def test_anonymize_instance_failure(client, test_session) -> None:
    """anonymize_dataset failure on one instance does not block others."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    patient = Patient(id="INST_FAIL_PAT", name="InstFail Patient", auto_id=61)
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
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = False
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

        response = await client.post(
            "/api/dicom/studies/1.2.601.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["instances_failed"] == 1
    assert data["instances_anonymized"] == 2


@pytest.mark.asyncio
async def test_anonymize_send_to_pacs_failure_resilient(client, test_session) -> None:
    """C-STORE failure in _send_to_pacs does not crash the workflow."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    patient = Patient(id="SEND_FAIL_PAT", name="SendFail Patient", auto_id=62)
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
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = True
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

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
async def test_anonymize_save_to_disk_error_graceful(client, test_session) -> None:
    """Disk save error is caught by gather(return_exceptions=True) — response is still 200."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    patient = Patient(id="DISK_FAIL_PAT", name="DiskFail Patient", auto_id=63)
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
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = True
        mock_settings.anon_send_to_pacs = False
        mock_settings.storage_path = "/tmp/test_anon"
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

        # Disk error is caught by gather(return_exceptions=True) — does not crash
        response = await client.post(
            "/api/dicom/studies/1.2.603.1/anonymize",
            json={"save_to_disk": True, "send_to_pacs": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["instances_anonymized"] == 1


@pytest.mark.asyncio
async def test_anonymize_study_no_series(client, test_session) -> None:
    """Study with no series returns valid result with zeros."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Study

    patient = Patient(id="NOSERIES_PAT", name="NoSeries Patient", auto_id=64)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id="NOSERIES_PAT",
        study_uid="1.2.604.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    with (
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = False
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

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
async def test_anonymize_all_series_filtered(client, test_session) -> None:
    """All series filtered out (SR/KO) results in zero anonymized."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    patient = Patient(id="ALLFILT_PAT", name="AllFilt Patient", auto_id=65)
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

    with (
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = False
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

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
async def test_anonymize_patient_not_found(client, test_session) -> None:
    """Study exists but patient does not → 404."""
    from datetime import UTC, datetime

    from clarinet.models.study import Study

    # Create study with a patient_id that doesn't exist as a Patient record
    study = Study(
        patient_id="GHOST_PAT",
        study_uid="1.2.606.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    with (
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = False
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

        response = await client.post(
            "/api/dicom/studies/1.2.606.1/anonymize",
            json={"save_to_disk": False, "send_to_pacs": False},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_anonymize_batch_cstore_partial_failure(client, test_session) -> None:
    """Batch C-STORE with partial failure reports correct send_failed count."""
    from datetime import UTC, datetime

    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study

    patient = Patient(id="BATCH_FAIL_PAT", name="BatchFail Patient", auto_id=66)
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
        patch("clarinet.services.anonymization_service.settings") as mock_settings,
        patch("clarinet.services.dicom.series_filter.settings") as mock_filter_settings,
    ):
        mock_settings.anon_uid_salt = "test-salt"
        mock_settings.anon_save_to_disk = False
        mock_settings.anon_send_to_pacs = True
        mock_filter_settings.series_filter_excluded_modalities = ["SR", "KO", "PR"]
        mock_filter_settings.series_filter_min_instance_count = None
        mock_filter_settings.series_filter_unknown_modality_policy = "include"

        response = await client.post(
            "/api/dicom/studies/1.2.607.1/anonymize",
            json={"send_to_pacs": True, "save_to_disk": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["instances_anonymized"] == 3
    assert data["instances_failed"] == 0
    assert data["instances_send_failed"] == 1
