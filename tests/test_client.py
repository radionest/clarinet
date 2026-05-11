"""
Tests for Clarinet API Client with real server.

This module provides integration tests for the ClarinetClient,
using real FastAPI server and database.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import orjson
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.client import ClarinetAPIError, ClarinetAuthError, ClarinetClient
from clarinet.models import RecordType
from clarinet.models.patient import Patient
from clarinet.models.record import Record
from clarinet.models.study import Series, Study
from clarinet.models.user import User
from tests.utils.factories import make_patient


class TestAuthentication:
    """Test authentication methods with real server."""

    @pytest.mark.asyncio
    async def test_login_success(
        self, clarinet_client: ClarinetClient, admin_user: User, test_session: AsyncSession
    ) -> None:
        """Test successful login with real server."""
        user = await clarinet_client.login(username=admin_user.email, password="adminpassword")

        assert clarinet_client._authenticated is True
        assert user.email == admin_user.email

    @pytest.mark.asyncio
    async def test_login_failure(self, clarinet_client: ClarinetClient, admin_user: User) -> None:
        """Test login failure with wrong password."""
        with pytest.raises(ClarinetAuthError):
            await clarinet_client.login(username=admin_user.email, password="wrongpassword")

    @pytest.mark.asyncio
    async def test_logout(
        self, clarinet_client: ClarinetClient, admin_user: User, test_session: AsyncSession
    ) -> None:
        """Test logout clears session."""
        # Login first
        await clarinet_client.login(username=admin_user.email, password="adminpassword")
        assert clarinet_client._authenticated is True

        # Logout
        await clarinet_client.logout()
        assert clarinet_client._authenticated is False

    @pytest.mark.asyncio
    async def test_get_me(
        self, clarinet_client: ClarinetClient, admin_user: User, test_session: AsyncSession
    ) -> None:
        """Test get current user."""
        # Login first
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Get current user
        user = await clarinet_client.get_me()

        assert user.email == admin_user.email
        assert str(user.id) == str(admin_user.id)


class TestPatientManagement:
    """Test patient management methods with real server."""

    @pytest.mark.asyncio
    async def test_get_patients(
        self,
        clarinet_client: ClarinetClient,
        admin_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test getting all patients."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Get patients
        patients = await clarinet_client.get_patients()

        assert len(patients) >= 1
        patient_ids = [p.id for p in patients]
        assert test_patient.id in patient_ids

    @pytest.mark.asyncio
    async def test_get_patient(
        self,
        clarinet_client: ClarinetClient,
        admin_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test getting patient by ID."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Get patient
        patient = await clarinet_client.get_patient(test_patient.id)

        assert patient.id == test_patient.id
        assert patient.name == test_patient.name

    @pytest.mark.asyncio
    async def test_create_patient(
        self,
        clarinet_client: ClarinetClient,
        admin_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test creating a patient."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Create patient
        patient_data = {"patient_id": "P_TEST_999", "patient_name": "Test Patient Created"}
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
        admin_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test anonymizing a patient."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Create patient without anon_name
        new_patient = make_patient("TEST_PAT_ANON", "Patient To Anonymize")
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
        admin_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test getting all studies."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Get studies
        studies = await clarinet_client.get_studies()

        assert len(studies) >= 1
        study_uids = [s.study_uid for s in studies]
        assert test_study.study_uid in study_uids

    @pytest.mark.asyncio
    async def test_get_study(
        self,
        clarinet_client: ClarinetClient,
        admin_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test getting study by UID."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Get study
        study = await clarinet_client.get_study(test_study.study_uid)

        assert study.study_uid == test_study.study_uid
        assert study.patient_id == test_study.patient_id

    @pytest.mark.asyncio
    async def test_create_study(
        self,
        clarinet_client: ClarinetClient,
        admin_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test creating a study."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

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
        admin_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test getting series for a study."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

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
        admin_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test creating a series."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

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
        admin_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test getting all record types."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Create a test record type
        record_type = RecordType(name="test-type", level="SERIES")
        test_session.add(record_type)
        await test_session.commit()

        # Get record types
        types = await clarinet_client.get_record_types()

        assert len(types) >= 1
        type_names = [t.name for t in types]
        assert "test-type" in type_names

    @pytest.mark.asyncio
    async def test_create_record_type(
        self,
        clarinet_client: ClarinetClient,
        admin_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test creating a record type."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Create record type
        type_data = {
            "name": "new-test-type",
            "level": "STUDY",
            "description": "Test type created via client",
        }
        record_type = await clarinet_client.create_record_type(
            type_data, constrain_unique_names=True
        )

        assert record_type.name == "new-test-type"
        assert record_type.level == "STUDY"

        # Verify in database
        db_type = await test_session.get(RecordType, "new-test-type")
        assert db_type is not None
        assert db_type.description == "Test type created via client"

    @pytest.mark.asyncio
    async def test_get_my_records(
        self,
        clarinet_client: ClarinetClient,
        admin_user: User,
        test_patient: Patient,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test getting records assigned to current user."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Create record type if not exists
        record_type = await test_session.get(RecordType, "test-record")
        if not record_type:
            record_type = RecordType(name="test-record", level="STUDY")
            test_session.add(record_type)
            await test_session.commit()

        # Create a record assigned to admin_user
        record = Record(
            status="pending",
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            record_type_name=record_type.name,
            user_id=admin_user.id,
        )
        test_session.add(record)
        await test_session.commit()

        # Get user's records via find_records with user_id filter
        records = await clarinet_client.find_records(user_id=str(admin_user.id))

        assert len(records) >= 1
        # Check that at least one record belongs to admin_user
        user_record_ids = [r.user_id for r in records if r.user_id]
        assert str(admin_user.id) in [str(uid) for uid in user_record_ids]


class TestHighLevelMethods:
    """Test high-level convenience methods with real server."""

    @pytest.mark.asyncio
    async def test_create_studies_batch(
        self,
        clarinet_client: ClarinetClient,
        admin_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test creating multiple studies."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

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
        admin_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test creating patient with studies."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Create patient with studies
        patient_data = {"patient_id": "P_BATCH", "patient_name": "Patient with Studies"}
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
        admin_user: User,
        test_study: Study,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test getting complete study hierarchy."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

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
        admin_user: User,
        test_study: Study,
        test_session: AsyncSession,
    ) -> None:
        """Test creating multiple series."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

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
        admin_user: User,
        test_patient: Patient,
        test_session: AsyncSession,
    ) -> None:
        """Test API error handling on duplicate patient."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Try to create duplicate patient
        patient_data = {"patient_id": test_patient.id, "patient_name": "Duplicate"}

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
        admin_user: User,
        test_session: AsyncSession,
    ) -> None:
        """Test not found error."""
        # Login
        await clarinet_client.login(username=admin_user.email, password="adminpassword")

        # Try to get non-existent patient
        with pytest.raises(ClarinetAPIError) as exc_info:
            await clarinet_client.get_patient("NONEXISTENT_PATIENT_ID")

        assert exc_info.value.status_code == 404


class TestRetryOn401:
    """Test auto-retry on 401 with re-login."""

    @pytest.mark.asyncio
    async def test_retry_on_401_re_authenticates(self) -> None:
        """First request returns 401, login succeeds, retry returns 200."""
        call_count = 0

        async def mock_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(401, text="Unauthorized")
            return httpx.Response(200, json={"ok": True})

        client = ClarinetClient(
            "http://test", username="admin@test.com", password="secret", auto_login=False
        )
        client.client = AsyncMock()
        client.client.request = mock_request

        with patch.object(client, "login", new_callable=AsyncMock) as mock_login:
            mock_login.return_value = None
            response = await client._request("GET", "/patients")

        assert response.status_code == 200
        mock_login.assert_called_once()
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_for_auth_endpoints(self) -> None:
        """401 on /auth/ endpoints raises immediately without retry."""

        async def mock_request(method, url, **kwargs):
            return httpx.Response(401, text="Unauthorized")

        client = ClarinetClient(
            "http://test", username="admin@test.com", password="secret", auto_login=False
        )
        client.client = AsyncMock()
        client.client.request = mock_request

        with (
            patch.object(client, "login", new_callable=AsyncMock) as mock_login,
            pytest.raises(ClarinetAuthError) as exc_info,
        ):
            await client._request("GET", "/auth/me")

        assert exc_info.value.status_code == 401
        mock_login.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_retry_without_credentials(self) -> None:
        """401 without credentials raises immediately without retry."""

        async def mock_request(method, url, **kwargs):
            return httpx.Response(401, text="Unauthorized")

        client = ClarinetClient("http://test", auto_login=False)
        client.client = AsyncMock()
        client.client.request = mock_request

        with pytest.raises(ClarinetAuthError) as exc_info:
            await client._request("GET", "/patients")

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_infinite_retry_loop(self) -> None:
        """If re-login succeeds but second request also returns 401, raises."""

        async def mock_request(method, url, **kwargs):
            return httpx.Response(401, text="Unauthorized")

        client = ClarinetClient(
            "http://test", username="admin@test.com", password="secret", auto_login=False
        )
        client.client = AsyncMock()
        client.client.request = mock_request

        with (
            patch.object(client, "login", new_callable=AsyncMock) as mock_login,
            pytest.raises(ClarinetAuthError),
        ):
            await client._request("GET", "/patients")

        mock_login.assert_called_once()


class TestJSONSerialization:
    """Regression: caller dicts with UUID/datetime must serialize without TypeError."""

    @pytest.mark.asyncio
    async def test_request_json_with_uuid_and_datetime(self) -> None:
        """submit_record_data-shaped payloads with UUID / datetime / nested lists go through."""
        captured: dict[str, bytes] = {}

        async def mock_request(method, url, **kwargs):
            captured["content"] = kwargs.get("content", b"")
            captured["content_type"] = kwargs.get("headers", {}).get("Content-Type", "")
            assert "json" not in kwargs, "client should not pass json= after orjson rewrite"
            return httpx.Response(200, json={"ok": True})

        client = ClarinetClient("http://test", auto_login=False)
        client.client = AsyncMock()
        client.client.request = mock_request

        source_id = uuid4()
        nested_id = uuid4()
        when = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

        await client._request(
            "POST",
            "/records/42/data",
            json={"source_id": source_id, "items": [nested_id], "at": when},
        )

        assert captured["content_type"] == "application/json"
        body = orjson.loads(captured["content"])
        assert body == {
            "source_id": str(source_id),
            "items": [str(nested_id)],
            "at": when.isoformat(),
        }

    @pytest.mark.asyncio
    async def test_request_without_json_kwarg_untouched(self) -> None:
        """When caller doesn't pass json=, the request goes through unchanged (e.g. form data)."""
        captured: dict[str, object] = {}

        async def mock_request(method, url, **kwargs):
            captured.update(kwargs)
            return httpx.Response(200, json={"ok": True})

        client = ClarinetClient("http://test", auto_login=False)
        client.client = AsyncMock()
        client.client.request = mock_request

        await client._request("POST", "/auth/login", data={"username": "u", "password": "p"})

        assert "content" not in captured
        assert captured["data"] == {"username": "u", "password": "p"}

    @pytest.mark.asyncio
    async def test_request_preserves_caller_headers(self) -> None:
        """Caller-supplied headers (Authorization, custom Content-Type) must survive."""
        captured: dict[str, object] = {}

        async def mock_request(method, url, **kwargs):
            captured.update(kwargs)
            return httpx.Response(200, json={"ok": True})

        client = ClarinetClient("http://test", auto_login=False)
        client.client = AsyncMock()
        client.client.request = mock_request

        await client._request(
            "POST",
            "/records/42/data",
            json={"k": "v"},
            headers={"Authorization": "Bearer xxx", "Content-Type": "application/vnd.api+json"},
        )

        headers = captured["headers"]
        assert headers["Authorization"] == "Bearer xxx"
        assert headers["Content-Type"] == "application/vnd.api+json"

    @pytest.mark.asyncio
    async def test_request_adds_content_type_when_headers_lack_it(self) -> None:
        """If caller passes headers without Content-Type, the client fills it in."""
        captured: dict[str, object] = {}

        async def mock_request(method, url, **kwargs):
            captured.update(kwargs)
            return httpx.Response(200, json={"ok": True})

        client = ClarinetClient("http://test", auto_login=False)
        client.client = AsyncMock()
        client.client.request = mock_request

        await client._request(
            "POST",
            "/records/42/data",
            json={"k": "v"},
            headers={"Authorization": "Bearer xxx"},
        )

        headers = captured["headers"]
        assert headers["Authorization"] == "Bearer xxx"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_request_headers_as_list_of_tuples(self) -> None:
        """httpx accepts headers= as a list of tuples — _request must handle it."""
        captured: dict[str, object] = {}

        async def mock_request(method, url, **kwargs):
            captured.update(kwargs)
            return httpx.Response(200, json={"ok": True})

        client = ClarinetClient("http://test", auto_login=False)
        client.client = AsyncMock()
        client.client.request = mock_request

        await client._request(
            "POST",
            "/records/42/data",
            json={"k": "v"},
            headers=[("X-Trace", "abc")],
        )

        headers = captured["headers"]
        assert headers["X-Trace"] == "abc"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_request_headers_as_httpx_headers_instance(self) -> None:
        """httpx.Headers instance from caller must be respected as-is."""
        captured: dict[str, object] = {}

        async def mock_request(method, url, **kwargs):
            captured.update(kwargs)
            return httpx.Response(200, json={"ok": True})

        client = ClarinetClient("http://test", auto_login=False)
        client.client = AsyncMock()
        client.client.request = mock_request

        await client._request(
            "POST",
            "/records/42/data",
            json={"k": "v"},
            headers=httpx.Headers({"X-Trace": "abc"}),
        )

        headers = captured["headers"]
        assert headers["X-Trace"] == "abc"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_request_rejects_simultaneous_json_and_content(self) -> None:
        """httpx forbids json= + content= together; client must fail loudly, not overwrite."""
        client = ClarinetClient("http://test", auto_login=False)
        client.client = AsyncMock()
        client.client.request = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))

        with pytest.raises(TypeError, match="either json= or content="):
            await client._request(
                "POST",
                "/records/42/data",
                json={"a": 1},
                content=b"raw-binary",
            )

        client.client.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_request_caller_content_type_case_insensitive(self) -> None:
        """A lowercase content-type from the caller must win over our default."""
        captured: dict[str, object] = {}

        async def mock_request(method, url, **kwargs):
            captured.update(kwargs)
            return httpx.Response(200, json={"ok": True})

        client = ClarinetClient("http://test", auto_login=False)
        client.client = AsyncMock()
        client.client.request = mock_request

        await client._request(
            "POST",
            "/records/42/data",
            json={"k": "v"},
            headers={"content-type": "application/xml"},
        )

        headers = captured["headers"]
        # httpx.Headers normalizes to a single case-insensitive entry.
        assert headers["Content-Type"] == "application/xml"
        assert headers["content-type"] == "application/xml"
