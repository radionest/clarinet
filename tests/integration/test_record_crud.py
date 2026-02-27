"""CRUD operations tests for Record."""

from datetime import UTC, datetime

import pytest
from sqlmodel import select

from src.models.record import Record, RecordStatus, RecordType


@pytest.mark.asyncio
async def test_create_record_type(test_session):
    """Test creating record type."""
    record_type = RecordType(
        name="Test Record Type",
        description="Test record description",
        data_schema={"type": "object", "properties": {"field1": {"type": "string"}}},
    )
    test_session.add(record_type)
    await test_session.commit()
    await test_session.refresh(record_type)

    assert record_type.name == "Test Record Type"
    assert record_type.description == "Test record description"
    assert record_type.data_schema is not None


@pytest.mark.asyncio
async def test_create_record(test_session, test_user, test_patient, test_study):
    """Test creating record."""
    # Create record type
    record_type = RecordType(
        name="Simple Record", description="Simple record", data_schema={"type": "object"}
    )
    test_session.add(record_type)
    await test_session.commit()

    # Create record
    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.pending,
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)

    assert record.id is not None
    assert record.user_id == test_user.id
    assert record.record_type_name == record_type.name
    assert record.status == RecordStatus.pending


@pytest.mark.asyncio
async def test_get_record_by_id(test_session, test_user, test_patient, test_study):
    """Test getting record by ID."""
    # Create record
    record_type = RecordType(
        name="Get Record", description="Get record", data_schema={"type": "object"}
    )
    test_session.add(record_type)
    await test_session.commit()

    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.inwork,
    )
    test_session.add(record)
    await test_session.commit()

    # Get record
    result = await test_session.get(Record, record.id)
    assert result is not None
    assert result.id == record.id
    assert result.status == RecordStatus.inwork


@pytest.mark.asyncio
async def test_update_record_status(test_session, test_user, test_patient, test_study):
    """Test updating record status."""
    # Create record
    record_type = RecordType(
        name="Update Record", description="Update record", data_schema={"type": "object"}
    )
    test_session.add(record_type)
    await test_session.commit()

    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.pending,
    )
    test_session.add(record)
    await test_session.commit()

    # Update status
    record.status = RecordStatus.finished
    record.finished_at = datetime.now(UTC)
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)

    # Check changes
    updated_record = await test_session.get(Record, record.id)
    assert updated_record.status == RecordStatus.finished
    assert updated_record.finished_at is not None


@pytest.mark.asyncio
async def test_delete_record(test_session, test_user, test_patient, test_study):
    """Test deleting record."""
    # Create record
    record_type = RecordType(
        name="Delete Record", description="Delete record", data_schema={"type": "object"}
    )
    test_session.add(record_type)
    await test_session.commit()

    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.pending,
    )
    test_session.add(record)
    await test_session.commit()
    record_id = record.id

    # Delete record
    await test_session.delete(record)
    await test_session.commit()

    # Check deletion
    deleted_record = await test_session.get(Record, record_id)
    assert deleted_record is None


@pytest.mark.asyncio
async def test_get_user_records(test_session, test_user, test_patient, test_study):
    """Test getting user records."""
    # Create multiple records for user
    record_type = RecordType(
        name="User Records", description="User records", data_schema={"type": "object"}
    )
    test_session.add(record_type)
    await test_session.commit()

    for _ in range(3):
        record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            user_id=test_user.id,
            record_type_name=record_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(record)

    await test_session.commit()

    # Get user records
    statement = select(Record).where(Record.user_id == test_user.id)
    result = await test_session.execute(statement)
    records = result.scalars().all()

    assert len(records) >= 3
    for record in records:
        assert record.user_id == test_user.id


@pytest.mark.asyncio
async def test_filter_records_by_status(test_session, test_user, test_patient, test_study):
    """Test filtering records by status."""
    # Create records with different statuses
    record_type = RecordType(
        name="Filter Records", description="Filter records", data_schema={"type": "object"}
    )
    test_session.add(record_type)
    await test_session.commit()

    statuses = [RecordStatus.pending, RecordStatus.inwork, RecordStatus.finished]
    for status in statuses:
        record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            user_id=test_user.id,
            record_type_name=record_type.name,
            status=status,
        )
        test_session.add(record)

    await test_session.commit()

    # Filter by PENDING status
    statement = select(Record).where(
        (Record.user_id == test_user.id) & (Record.status == RecordStatus.pending)
    )
    result = await test_session.execute(statement)
    pending_records = result.scalars().all()

    assert len(pending_records) >= 1
    for record in pending_records:
        assert record.status == RecordStatus.pending


@pytest.mark.asyncio
async def test_record_type_with_multiple_records(test_session, test_user, admin_user):
    """Test creating multiple records for one type."""
    # Create record type
    record_type = RecordType(
        name="Shared Record Type",
        description="Shared record",
        data_schema={
            "type": "object",
            "properties": {"difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]}},
        },
    )
    test_session.add(record_type)
    await test_session.commit()

    # Create necessary objects
    from src.models.patient import Patient
    from src.models.study import Study

    patient = Patient(id="RECORD_PAT007", name="Multiple Records Patient", anon_name="ANON_REC_007")
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.RECORD.7",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_RECORD_STUDY_007",
    )
    test_session.add(study)
    await test_session.commit()

    # Create records for different users
    record1 = Record(
        patient_id=patient.id,
        study_uid=study.study_uid,
        user_id=test_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.pending,
        data={"difficulty": "easy"},
    )
    record2 = Record(
        patient_id=patient.id,
        study_uid=study.study_uid,
        user_id=admin_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.inwork,
        data={"difficulty": "hard"},
    )

    test_session.add(record1)
    test_session.add(record2)
    await test_session.commit()

    # Get all records of this type
    statement = select(Record).where(Record.record_type_name == record_type.name)
    result = await test_session.execute(statement)
    records = result.scalars().all()

    assert len(records) == 2
    user_ids = [record.user_id for record in records]
    assert test_user.id in user_ids
    assert admin_user.id in user_ids


@pytest.mark.asyncio
async def test_record_data_json_field(test_session, test_user, test_patient, test_study):
    """Test working with JSON field data in record."""
    # Create record type with JSON schema
    record_type = RecordType(
        name="JSON Record",
        description="JSON record",
        data_schema={
            "type": "object",
            "properties": {
                "labels": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
            },
        },
    )
    test_session.add(record_type)
    await test_session.commit()

    # Create record with JSON data
    record_data = {"labels": ["cat", "dog", "bird"], "confidence": 0.95}

    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.pending,
        data=record_data,
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)

    # Check JSON data
    stored_data = record.data or {}
    assert stored_data["labels"] == ["cat", "dog", "bird"]
    assert stored_data["confidence"] == 0.95


@pytest.mark.asyncio
async def test_submit_record_data_no_lazy_load(
    fresh_client, test_session, test_user, test_patient, test_study, test_record_type
):
    """Verify submit_record_data eagerly loads all needed relationships.

    Regression test: uses fresh_client (separate session) to detect
    MissingGreenlet errors from lazy-loading in async context.
    """
    # Create record in test_session (populates DB)
    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=test_record_type.name,
        status=RecordStatus.pending,
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)

    # Login via fresh_client (separate cookie jar)
    login_resp = await fresh_client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpassword"},
    )
    assert login_resp.status_code in [200, 204]

    # fresh_client uses a DIFFERENT session (empty identity map)
    # If endpoint doesn't eagerly load study/series, this will fail with MissingGreenlet
    response = await fresh_client.post(
        f"/api/records/{record.id}/data",
        json={},
    )
    # Should not be a 500 server error (MissingGreenlet)
    assert response.status_code != 500


@pytest.mark.asyncio
async def test_validate_files_no_lazy_load(
    fresh_client, test_session, test_user, test_patient, test_study, test_record_type
):
    """Verify validate_files_endpoint eagerly loads all needed relationships.

    Regression test: uses fresh_client (separate session) to detect
    MissingGreenlet errors from lazy-loading in async context.
    """
    # Create record in test_session (populates DB)
    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=test_record_type.name,
        status=RecordStatus.pending,
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)

    # Login via fresh_client (separate cookie jar)
    login_resp = await fresh_client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpassword"},
    )
    assert login_resp.status_code in [200, 204]

    # fresh_client uses a DIFFERENT session (empty identity map)
    response = await fresh_client.post(
        f"/api/records/{record.id}/validate-files",
    )
    # Should not be a 500 server error (MissingGreenlet)
    assert response.status_code != 500
