"""
Tests for Clarinet API Client with real server.

This module provides integration tests for the ClarinetClient,
using real FastAPI server and database.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.client import ClarinetAPIError, ClarinetAuthError, ClarinetClient
from src.models import RecordType
from src.models.patient import Patient
from src.models.record import Record
from src.models.study import Series, Study
from src.models.user import User


class TestAuthentication:
    """Test authentication methods with real server."""

    @pytest.mark.asyncio
    async def test_login_success(
        self, clarinet_client: ClarinetClient, test_user: User, test_session: AsyncSession
    ) -> None:
        """Test successful login with real server."""
        user = await clarinet_client.login(username=test_user.email, password="testpassword")

        assert clarinet_client._authenticated is True
        assert user.email == test_user.email

    @pytest.mark.asyncio
    async def test_login_failure(self, clarinet_client: ClarinetClient, test_user: User) -> None:
        """Test login failure with wrong password."""
        with pytest.raises(ClarinetAuthError):
            await clarinet_client.login(username=test_user.email, password="wrongpassword")

    @pytest.mark.asyncio
    async def test_logout(
        self, clarinet_client: ClarinetClient, test_user: User, test_session: AsyncSession
    ) -> None:
        """Test logout clears session."""
        # Login first
        await clarinet_client.login(username=test_user.email, password="testpassword")
        assert clarinet_client._authenticated is True

        # Logout
        await clarinet_client.logout()
        assert clarinet_client._authenticated is False

    @pytest.mark.asyncio
    async def test_get_me(
        self, clarinet_client: ClarinetClient, test_user: User, test_session: AsyncSession
    ) -> None:
        """Test get current user."""
        # Login first
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Get current user
        user = await clarinet_client.get_me()

        assert user.email == test_user.email
        assert str(user.id) == str(test_user.id)


class TestPatientManagement:
    """Test patient management methods with real server."""

    @pytest.mark.asyncio
    async def test_get_patients(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test getting all patients."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Get patients
        patients = await clarinet_client.get_patients()

        assert len(patients) >= 1
        patient_ids = [p.id for p in patients]
        assert test_patient.id in patient_ids

    @pytest.mark.asyncio
    async def test_get_patient(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test getting patient by ID."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Get patient
        patient = await clarinet_client.get_patient(test_patient.id)

        assert patient.id == test_patient.id
        assert patient.name == test_patient.name

    @pytest.mark.asyncio
    async def test_create_patient(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test creating a patient."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create patient
        patient_data = {"id": "P_TEST_999", "name": "Test Patient Created"}
        patient = await clarinet_client.create_patient(patient_data)

        assert patient.id == "P_TEST_999"
        assert patient.name == "Test Patient Created"

        # Verify in database
        db_patient = await test_session.get(Patient, "P_TEST_999")
        assert db_patient is not None
        assert db_patient.name == "Test Patient Created"

    @pytest.mark.asyncio
    async def test_anonymize_patient(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test anonymizing a patient."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create patient without anon_name
        new_patient = Patient(id="TEST_PAT_ANON", name="Patient To Anonymize")
        test_session.add(new_patient)
        await test_session.commit()

        # Anonymize patient
        patient = await clarinet_client.anonymize_patient(new_patient.id)

        # Should have anonymous name assigned
        assert patient.anon_name is not None


class TestStudyManagement:
    """Test study management methods with real server."""

    @pytest.mark.asyncio
    async def test_get_studies(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test getting all studies."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Get studies
        studies = await clarinet_client.get_studies()

        assert len(studies) >= 1
        study_uids = [s.study_uid for s in studies]
        assert test_study.study_uid in study_uids

    @pytest.mark.asyncio
    async def test_get_study(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test getting study by UID."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Get study
        study = await clarinet_client.get_study(test_study.study_uid)

        assert study.study_uid == test_study.study_uid
        assert study.patient_id == test_study.patient_id

    @pytest.mark.asyncio
    async def test_create_study(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test creating a study."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create study
        study_data = {
            "study_uid": "1.2.3.4.5.999",
            "date": datetime.now(UTC).date().isoformat(),
            "patient_id": test_patient.id,
        }
        study = await clarinet_client.create_study(study_data)

        assert study.study_uid == "1.2.3.4.5.999"
        assert study.patient_id == test_patient.id

        # Verify in database
        db_study = await test_session.get(Study, "1.2.3.4.5.999")
        assert db_study is not None
        assert db_study.patient_id == test_patient.id

    @pytest.mark.asyncio
    async def test_get_study_series(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test getting series for a study."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create a series for the test study
        series = Series(
            study_uid=test_study.study_uid,
            series_uid=f"{test_study.study_uid}.1",
            series_number=1,
            series_description="Test Series",
        )
        test_session.add(series)
        await test_session.commit()

        # Get study series
        series_list = await clarinet_client.get_study_series(test_study.study_uid)

        assert len(series_list) >= 1
        series_uids = [s.series_uid for s in series_list]
        assert f"{test_study.study_uid}.1" in series_uids


class TestSeriesManagement:
    """Test series management methods with real server."""

    @pytest.mark.asyncio
    async def test_create_series(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test creating a series."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create series
        series_data = {
            "series_uid": f"{test_study.study_uid}.999",
            "series_number": 999,
            "study_uid": test_study.study_uid,
            "series_description": "Created Test Series",
        }
        series = await clarinet_client.create_series(series_data)

        assert series.series_uid == f"{test_study.study_uid}.999"
        assert series.series_number == 999

        # Verify in database
        db_series = await test_session.get(Series, f"{test_study.study_uid}.999")
        assert db_series is not None
        assert db_series.series_description == "Created Test Series"


class TestRecordManagement:
    """Test record management methods with real server."""

    @pytest.mark.asyncio
    async def test_get_record_types(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test getting all record types."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create a test record type
        record_type = RecordType(name="test_type", level="SERIES")
        test_session.add(record_type)
        await test_session.commit()

        # Get record types
        types = await clarinet_client.get_record_types()

        assert len(types) >= 1
        type_names = [t.name for t in types]
        assert "test_type" in type_names

    @pytest.mark.asyncio
    async def test_create_record_type(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test creating a record type."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create record type
        type_data = {
            "name": "new_test_type",
            "level": "STUDY",
            "description": "Test type created via client",
        }
        record_type = await clarinet_client.create_record_type(
            type_data, constrain_unique_names=True
        )

        assert record_type.name == "new_test_type"
        assert record_type.level == "STUDY"

        # Verify in database
        db_type = await test_session.get(RecordType, "new_test_type")
        assert db_type is not None
        assert db_type.description == "Test type created via client"

    @pytest.mark.asyncio
    async def test_get_my_records(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_patient: Patient,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test getting records assigned to current user."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create record type if not exists
        record_type = await test_session.get(RecordType, "test_record")
        if not record_type:
            record_type = RecordType(name="test_record", level="STUDY")
            test_session.add(record_type)
            await test_session.commit()

        # Create a record assigned to test_user
        record = Record(
            status="pending",
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            record_type_name=record_type.name,
            user_id=test_user.id,
        )
        test_session.add(record)
        await test_session.commit()

        # Get user's records
        records = await clarinet_client.get_my_records()

        assert len(records) >= 1
        # Check that at least one record belongs to test_user
        user_record_ids = [r.user_id for r in records if r.user_id]
        assert str(test_user.id) in [str(uid) for uid in user_record_ids]


class TestHighLevelMethods:
    """Test high-level convenience methods with real server."""

    @pytest.mark.asyncio
    async def test_create_studies_batch(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test creating multiple studies."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create studies batch
        studies_data = [
            {
                "study_uid": "1.2.3.888.1",
                "date": datetime.now(UTC).date().isoformat(),
                "patient_id": test_patient.id,
            },
            {
                "study_uid": "1.2.3.888.2",
                "date": datetime.now(UTC).date().isoformat(),
                "patient_id": test_patient.id,
            },
        ]

        studies = await clarinet_client.create_studies_batch(studies_data)

        assert len(studies) == 2
        assert studies[0].study_uid == "1.2.3.888.1"
        assert studies[1].study_uid == "1.2.3.888.2"

        # Verify in database
        db_study1 = await test_session.get(Study, "1.2.3.888.1")
        db_study2 = await test_session.get(Study, "1.2.3.888.2")
        assert db_study1 is not None
        assert db_study2 is not None

    @pytest.mark.asyncio
    async def test_create_patient_with_studies(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test creating patient with studies."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create patient with studies
        patient_data = {"id": "P_BATCH", "name": "Patient with Studies"}
        studies_data = [
            {"study_uid": "1.2.3.777.1", "date": datetime.now(UTC).date().isoformat()},
            {"study_uid": "1.2.3.777.2", "date": datetime.now(UTC).date().isoformat()},
        ]

        patient, studies = await clarinet_client.create_patient_with_studies(
            patient_data, studies_data
        )

        assert patient.id == "P_BATCH"
        assert len(studies) == 2
        assert all(s.patient_id == "P_BATCH" for s in studies)

        # Verify in database
        db_patient = await test_session.get(Patient, "P_BATCH")
        assert db_patient is not None

        db_study1 = await test_session.get(Study, "1.2.3.777.1")
        db_study2 = await test_session.get(Study, "1.2.3.777.2")
        assert db_study1 is not None
        assert db_study2 is not None
        assert db_study1.patient_id == "P_BATCH"
        assert db_study2.patient_id == "P_BATCH"

    @pytest.mark.asyncio
    async def test_get_study_hierarchy(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_study: Study,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test getting complete study hierarchy."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create a series for the study
        series = Series(
            study_uid=test_study.study_uid,
            series_uid=f"{test_study.study_uid}.555",
            series_number=100,
            series_description="Hierarchy Test",
        )
        test_session.add(series)
        await test_session.commit()

        # Get hierarchy
        hierarchy = await clarinet_client.get_study_hierarchy(test_study.study_uid)

        assert "study" in hierarchy
        assert "patient" in hierarchy
        assert "series" in hierarchy
        assert "records" in hierarchy

        assert hierarchy["study"]["study_uid"] == test_study.study_uid
        assert hierarchy["patient"]["id"] == test_patient.id
        assert len(hierarchy["series"]) >= 1

    @pytest.mark.asyncio
    async def test_create_series_batch(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test creating multiple series."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Create series batch
        series_data = [
            {
                "series_uid": f"{test_study.study_uid}.666.1",
                "series_number": 201,
                "study_uid": test_study.study_uid,
            },
            {
                "series_uid": f"{test_study.study_uid}.666.2",
                "series_number": 202,
                "study_uid": test_study.study_uid,
            },
        ]

        series_list = await clarinet_client.create_series_batch(series_data)

        assert len(series_list) == 2
        assert series_list[0].series_number == 201
        assert series_list[1].series_number == 202

        # Verify in database
        db_series1 = await test_session.get(Series, f"{test_study.study_uid}.666.1")
        db_series2 = await test_session.get(Series, f"{test_study.study_uid}.666.2")
        assert db_series1 is not None
        assert db_series2 is not None


class TestErrorHandling:
    """Test error handling with real server."""

    @pytest.mark.asyncio
    async def test_api_error_on_duplicate(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test API error handling on duplicate patient."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Try to create duplicate patient
        patient_data = {"id": test_patient.id, "name": "Duplicate"}

        with pytest.raises(ClarinetAPIError) as exc_info:
            await clarinet_client.create_patient(patient_data)

        assert exc_info.value.status_code >= 400

    @pytest.mark.asyncio
    async def test_auth_error_without_login(
        self, clarinet_client: ClarinetClient, test_session: AsyncSession
    ) -> None:
        """Test authentication error when not logged in."""
        # Try to access protected endpoint without login
        with pytest.raises(ClarinetAuthError):
            await clarinet_client.get_me()

    @pytest.mark.asyncio
    async def test_not_found_error(
        self,
        clarinet_client: ClarinetClient,
        test_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test not found error."""
        # Login
        await clarinet_client.login(username=test_user.email, password="testpassword")

        # Try to get non-existent patient
        with pytest.raises(ClarinetAPIError) as exc_info:
            await clarinet_client.get_patient("NONEXISTENT_PATIENT_ID")

        assert exc_info.value.status_code == 404
