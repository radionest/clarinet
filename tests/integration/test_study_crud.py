"""Тесты CRUD операций для Study, Patient и Series."""

from datetime import date

import pytest
from sqlmodel import select

from src.models.patient import Patient
from src.models.study import Series, Study


@pytest.mark.asyncio
async def test_create_patient(test_session):
    """Тест создания пациента."""
    patient = Patient(
        id="PAT001",
        name="John Doe",
        anon_name="ANON_001"
    )
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)

    assert patient.id == "PAT001"
    assert patient.name == "John Doe"
    assert patient.anon_name == "ANON_001"


@pytest.mark.asyncio
async def test_get_patient_by_id(test_session):
    """Тест получения пациента по ID."""
    patient = Patient(
        id="PAT002",
        name="Jane Smith",
        anon_name="ANON_002"
    )
    test_session.add(patient)
    await test_session.commit()

    result = await test_session.get(Patient, patient.id)
    assert result is not None
    assert result.id == "PAT002"
    assert result.name == "Jane Smith"


@pytest.mark.asyncio
async def test_create_study(test_session):
    """Тест создания исследования."""
    # Создаем пациента
    patient = Patient(
        id="PAT003",
        name="Bob Johnson",
        anon_name="ANON_003"
    )
    test_session.add(patient)
    await test_session.commit()

    # Создаем исследование
    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.6",
        date=date.today(),
        anon_uid="ANON_STUDY_001"
    )
    test_session.add(study)
    await test_session.commit()
    await test_session.refresh(study)

    assert study.study_uid == "1.2.3.4.5.6"
    assert study.patient_id == patient.id
    assert study.date == date.today()
    assert study.anon_uid == "ANON_STUDY_001"


@pytest.mark.asyncio
async def test_create_series(test_session):
    """Тест создания серии."""
    # Создаем пациента и исследование
    patient = Patient(
        id="PAT004",
        name="Alice Brown",
        anon_name="ANON_004"
    )
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.7",
        date=date.today(),
        anon_uid="ANON_STUDY_002"
    )
    test_session.add(study)
    await test_session.commit()

    # Создаем серию
    series = Series(
        study_uid=study.study_uid,
        series_uid="1.2.3.4.5.7.1",
        series_number=1,
        series_description="T1 Axial",
        anon_uid="ANON_SERIES_001"
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
    """Тест обновления пациента."""
    patient = Patient(
        id="PAT005",
        name="Original Name",
        anon_name="ANON_005"
    )
    test_session.add(patient)
    await test_session.commit()

    # Обновляем данные
    patient.name = "Updated Name"
    patient.anon_name = "ANON_005_UPDATED"
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)

    # Проверяем изменения
    updated_patient = await test_session.get(Patient, patient.id)
    assert updated_patient.name == "Updated Name"
    assert updated_patient.anon_name == "ANON_005_UPDATED"


@pytest.mark.asyncio
async def test_delete_patient_cascade(test_session):
    """Тест каскадного удаления пациента с исследованиями."""
    # Создаем пациента с исследованием
    patient = Patient(
        id="PAT006",
        name="Delete Test",
        anon_name="ANON_006"
    )
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.8",
        date=date.today(),
        anon_uid="ANON_STUDY_003"
    )
    test_session.add(study)
    await test_session.commit()

    patient_id = patient.id
    study_uid = study.study_uid

    # Сначала удаляем связанные исследования
    await test_session.delete(study)
    await test_session.commit()
    
    # Затем удаляем пациента
    await test_session.delete(patient)
    await test_session.commit()

    # Проверяем что пациент удален
    deleted_patient = await test_session.get(Patient, patient_id)
    assert deleted_patient is None
    
    # Проверяем что исследование удалено
    deleted_study = await test_session.get(Study, study_uid)
    assert deleted_study is None


@pytest.mark.asyncio
async def test_get_patient_studies(test_session):
    """Тест получения всех исследований пациента."""
    # Создаем пациента
    patient = Patient(
        id="PAT007",
        name="Multi Study",
        anon_name="ANON_007"
    )
    test_session.add(patient)
    await test_session.commit()

    # Создаем несколько исследований
    for i in range(3):
        study = Study(
            patient_id=patient.id,
            study_uid=f"1.2.3.4.5.9.{i}",
            date=date.today(),
            anon_uid=f"ANON_STUDY_{i+4}"
        )
        test_session.add(study)

    await test_session.commit()

    # Получаем все исследования пациента
    statement = select(Study).where(Study.patient_id == patient.id)
    result = await test_session.execute(statement)
    studies = result.scalars().all()

    assert len(studies) == 3
    for study in studies:
        assert study.patient_id == patient.id


@pytest.mark.asyncio
async def test_get_study_series(test_session):
    """Тест получения всех серий исследования."""
    # Создаем структуру данных
    patient = Patient(
        id="PAT008",
        name="Series Test",
        anon_name="ANON_008"
    )
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.10",
        date=date.today(),
        anon_uid="ANON_STUDY_007"
    )
    test_session.add(study)
    await test_session.commit()

    # Создаем несколько серий
    for i in range(4):
        series = Series(
            study_uid=study.study_uid,
            series_uid=f"1.2.3.4.5.10.{i}",
            series_number=i+1,
            series_description=f"Series {i+1}",
            anon_uid=f"ANON_SERIES_{i+2}"
        )
        test_session.add(series)

    await test_session.commit()

    # Получаем все серии исследования
    statement = select(Series).where(Series.study_uid == study.study_uid)
    result = await test_session.execute(statement)
    series_list = result.scalars().all()

    assert len(series_list) == 4
    for series in series_list:
        assert series.study_uid == study.study_uid


@pytest.mark.asyncio
async def test_filter_studies_by_modality(test_session):
    """Тест фильтрации исследований по модальности."""
    patient = Patient(
        id="PAT009",
        name="Modality Test",
        anon_name="ANON_009"
    )
    test_session.add(patient)
    await test_session.commit()

    # Создаем исследования
    for i in range(4):
        study = Study(
            patient_id=patient.id,
            study_uid=f"1.2.3.4.5.11.{i}",
            date=date.today(),
            anon_uid=f"ANON_STUDY_{i+8}"
        )
        test_session.add(study)

    await test_session.commit()

    # Получаем все исследования пациента
    statement = select(Study).where(Study.patient_id == patient.id)
    result = await test_session.execute(statement)
    studies = result.scalars().all()

    assert len(studies) == 4


@pytest.mark.asyncio
async def test_patient_with_full_hierarchy(test_session):
    """Тест создания полной иерархии: пациент -> исследование -> серии."""
    # Создаем пациента
    patient = Patient(
        id="PAT010",
        name="Full Hierarchy",
        anon_name="ANON_010"
    )
    test_session.add(patient)
    await test_session.commit()

    # Создаем исследование
    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.12",
        date=date.today(),
        anon_uid="ANON_STUDY_012"
    )
    test_session.add(study)
    await test_session.commit()

    # Создаем серии
    series_data = [
        ("T1", "1.2.3.4.5.12.1"),
        ("T2", "1.2.3.4.5.12.2"),
        ("FLAIR", "1.2.3.4.5.12.3")
    ]

    for i, (desc, uid) in enumerate(series_data):
        series = Series(
            study_uid=study.study_uid,
            series_uid=uid,
            series_number=i+1,
            series_description=desc,
            anon_uid=f"ANON_SERIES_{i+10}"
        )
        test_session.add(series)

    await test_session.commit()

    # Проверяем структуру
    # Проверяем пациента
    stored_patient = await test_session.get(Patient, patient.id)
    assert stored_patient.id == "PAT010"

    # Проверяем исследование
    statement = select(Study).where(Study.patient_id == patient.id)
    result = await test_session.execute(statement)
    studies = result.scalars().all()
    assert len(studies) == 1
    assert studies[0].date == date.today()

    # Проверяем серии
    statement = select(Series).where(Series.study_uid == study.study_uid)
    result = await test_session.execute(statement)
    series_list = result.scalars().all()
    assert len(series_list) == 3
    series_descriptions = [s.series_description for s in series_list]
    assert "T1" in series_descriptions
    assert "T2" in series_descriptions
    assert "FLAIR" in series_descriptions
