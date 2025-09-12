"""CRUD operations tests for Study, Patient and Series."""

from datetime import UTC, datetime

import pytest
from sqlmodel import select

from src.models.patient import Patient
from src.models.study import Series, Study


@pytest.mark.asyncio
async def test_create_patient(test_session):
    """Test patient creation."""
    patient = Patient(id="PAT001", name="John Doe", anon_name="ANON_001")
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)

    assert patient.id == "PAT001"
    assert patient.name == "John Doe"
    assert patient.anon_name == "ANON_001"


@pytest.mark.asyncio
async def test_get_patient_by_id(test_session):
    """Test getting patient by ID."""
    patient = Patient(id="PAT002", name="Jane Smith", anon_name="ANON_002")
    test_session.add(patient)
    await test_session.commit()

    result = await test_session.get(Patient, patient.id)
    assert result is not None
    assert result.id == "PAT002"
    assert result.name == "Jane Smith"


@pytest.mark.asyncio
async def test_create_study(test_session):
    """Test study creation."""
    # Create patient
    patient = Patient(id="PAT003", name="Bob Johnson", anon_name="ANON_003")
    test_session.add(patient)
    await test_session.commit()

    # Create study
    study = Study(
        patient_id=patient.id, study_uid="1.2.3.4.5.6", date=datetime.now(UTC).date(), anon_uid="ANON_STUDY_001"
    )
    test_session.add(study)
    await test_session.commit()
    await test_session.refresh(study)

    assert study.study_uid == "1.2.3.4.5.6"
    assert study.patient_id == patient.id
    assert study.date == datetime.now(UTC).date()
    assert study.anon_uid == "ANON_STUDY_001"


@pytest.mark.asyncio
async def test_create_series(test_session):
    """Test series creation."""
    # Create patient and study
    patient = Patient(id="PAT004", name="Alice Brown", anon_name="ANON_004")
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient.id, study_uid="1.2.3.4.5.7", date=datetime.now(UTC).date(), anon_uid="ANON_STUDY_002"
    )
    test_session.add(study)
    await test_session.commit()

    # Create series
    series = Series(
        study_uid=study.study_uid,
        series_uid="1.2.3.4.5.7.1",
        series_number=1,
        series_description="T1 Axial",
        anon_uid="ANON_SERIES_001",
    )
    test_session.add(series)
    await test_session.commit()
    await test_session.refresh(series)

    assert series.series_uid == "1.2.3.4.5.7.1"
    assert series.study_uid == study.study_uid
    assert series.series_description == "T1 Axial"
    assert series.series_number == 1


@pytest.mark.asyncio
async def test_update_patient(test_session):
    """Test patient update."""
    patient = Patient(id="PAT005", name="Original Name", anon_name="ANON_005")
    test_session.add(patient)
    await test_session.commit()

    # Update data
    patient.name = "Updated Name"
    patient.anon_name = "ANON_005_UPDATED"
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)

    # Check changes
    updated_patient = await test_session.get(Patient, patient.id)
    assert updated_patient.name == "Updated Name"
    assert updated_patient.anon_name == "ANON_005_UPDATED"


@pytest.mark.asyncio
async def test_delete_patient_cascade(test_session):
    """Test cascade deletion of patient with studies."""
    # Create patient with study
    patient = Patient(id="PAT006", name="Delete Test", anon_name="ANON_006")
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient.id, study_uid="1.2.3.4.5.8", date=datetime.now(UTC).date(), anon_uid="ANON_STUDY_003"
    )
    test_session.add(study)
    await test_session.commit()

    patient_id = patient.id
    study_uid = study.study_uid

    # First delete related studies
    await test_session.delete(study)
    await test_session.commit()

    # Then delete patient
    await test_session.delete(patient)
    await test_session.commit()

    # Check that patient is deleted
    deleted_patient = await test_session.get(Patient, patient_id)
    assert deleted_patient is None

    # Check that study is deleted
    deleted_study = await test_session.get(Study, study_uid)
    assert deleted_study is None


@pytest.mark.asyncio
async def test_get_patient_studies(test_session):
    """Test getting all patient studies."""
    # Create patient
    patient = Patient(id="PAT007", name="Multi Study", anon_name="ANON_007")
    test_session.add(patient)
    await test_session.commit()

    # Create multiple studies
    for i in range(3):
        study = Study(
            patient_id=patient.id,
            study_uid=f"1.2.3.4.5.9.{i}",
            date=datetime.now(UTC).date(),
            anon_uid=f"ANON_STUDY_{i + 4}",
        )
        test_session.add(study)

    await test_session.commit()

    # Get all patient studies
    statement = select(Study).where(Study.patient_id == patient.id)
    result = await test_session.execute(statement)
    studies = result.scalars().all()

    assert len(studies) == 3
    for study in studies:
        assert study.patient_id == patient.id


@pytest.mark.asyncio
async def test_get_study_series(test_session):
    """Test getting all study series."""
    # Create data structure
    patient = Patient(id="PAT008", name="Series Test", anon_name="ANON_008")
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.10",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_STUDY_007",
    )
    test_session.add(study)
    await test_session.commit()

    # Create multiple series
    for i in range(4):
        series = Series(
            study_uid=study.study_uid,
            series_uid=f"1.2.3.4.5.10.{i}",
            series_number=i + 1,
            series_description=f"Series {i + 1}",
            anon_uid=f"ANON_SERIES_{i + 2}",
        )
        test_session.add(series)

    await test_session.commit()

    # Get all study series
    statement = select(Series).where(Series.study_uid == study.study_uid)
    result = await test_session.execute(statement)
    series_list = result.scalars().all()

    assert len(series_list) == 4
    for series in series_list:
        assert series.study_uid == study.study_uid


@pytest.mark.asyncio
async def test_filter_studies_by_modality(test_session):
    """Test filtering studies by modality."""
    patient = Patient(id="PAT009", name="Modality Test", anon_name="ANON_009")
    test_session.add(patient)
    await test_session.commit()

    # Create studies
    for i in range(4):
        study = Study(
            patient_id=patient.id,
            study_uid=f"1.2.3.4.5.11.{i}",
            date=datetime.now(UTC).date(),
            anon_uid=f"ANON_STUDY_{i + 8}",
        )
        test_session.add(study)

    await test_session.commit()

    # Get all patient studies
    statement = select(Study).where(Study.patient_id == patient.id)
    result = await test_session.execute(statement)
    studies = result.scalars().all()

    assert len(studies) == 4


@pytest.mark.asyncio
async def test_patient_with_full_hierarchy(test_session):
    """Test creating full hierarchy: patient -> study -> series."""
    # Create patient
    patient = Patient(id="PAT010", name="Full Hierarchy", anon_name="ANON_010")
    test_session.add(patient)
    await test_session.commit()

    # Create study
    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.12",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_STUDY_012",
    )
    test_session.add(study)
    await test_session.commit()

    # Create series
    series_data = [("T1", "1.2.3.4.5.12.1"), ("T2", "1.2.3.4.5.12.2"), ("FLAIR", "1.2.3.4.5.12.3")]

    for i, (desc, uid) in enumerate(series_data):
        series = Series(
            study_uid=study.study_uid,
            series_uid=uid,
            series_number=i + 1,
            series_description=desc,
            anon_uid=f"ANON_SERIES_{i + 10}",
        )
        test_session.add(series)

    await test_session.commit()

    # Check structure
    # Check patient
    stored_patient = await test_session.get(Patient, patient.id)
    assert stored_patient.id == "PAT010"

    # Check study
    statement = select(Study).where(Study.patient_id == patient.id)
    result = await test_session.execute(statement)
    studies = result.scalars().all()
    assert len(studies) == 1
    assert studies[0].date == datetime.now(UTC).date()

    # Check series
    statement = select(Series).where(Series.study_uid == study.study_uid)
    result = await test_session.execute(statement)
    series_list = result.scalars().all()
    assert len(series_list) == 3
    series_descriptions = [s.series_description for s in series_list]
    assert "T1" in series_descriptions
    assert "T2" in series_descriptions
    assert "FLAIR" in series_descriptions
