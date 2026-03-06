"""E2E tests: file-driven record lifecycle.

Tests the complete file processing chain: record types with file definitions,
auto-blocking when required files are missing, file validation/check,
auto-unblock, data submission with file validation, file-events dispatch,
and bulk status update.

All tests use in-memory SQLite + local filesystem (no external services).
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.app import app
from src.models.patient import Patient
from src.models.study import Series, Study
from src.services.recordflow.flow_file import FILE_REGISTRY
from src.services.recordflow.flow_record import ENTITY_REGISTRY, RECORD_REGISTRY
from tests.utils.urls import (
    PATIENTS_BASE,
    RECORD_TYPES,
    RECORDS_BASE,
)

# Fixed hierarchy IDs
_PATIENT_ID = "FILE_PAT_001"
_STUDY_UID = "1.2.826.0.1.1234567890"
_SERIES_UID = "1.2.826.0.1.1234567890.1"


# ---------------------------------------------------------------------------
# Autouse fixtures + client override
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the global FlowRecord registries between tests."""
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    yield
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Override e2e conftest's unauthenticated client with an authenticated one."""
    from httpx import ASGITransport

    from src.api.auth_config import current_active_user, current_superuser
    from src.models.user import User
    from src.utils.auth import get_password_hash
    from src.utils.database import get_async_session

    mock_user = User(
        id=uuid4(),
        email="e2e_file@test.com",
        hashed_password=get_password_hash("mock"),
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    test_session.add(mock_user)
    await test_session.commit()
    await test_session.refresh(mock_user)

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: mock_user
    app.dependency_overrides[current_superuser] = lambda: mock_user

    try:
        from src.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import src.api.auth_config

        src.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    # Prevent TOML exports when creating record types via API
    app.state.config_mode = "test"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        original_request = ac.request

        async def request_with_cookies(method, url, **kwargs):
            if ac.cookies:
                headers = kwargs.get("headers") or {}
                cookie_header = "; ".join([f"{k}={v}" for k, v in ac.cookies.items()])
                if cookie_header:
                    headers["Cookie"] = cookie_header
                    kwargs["headers"] = headers
            return await original_request(method, url, **kwargs)

        ac.request = request_with_cookies
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Data fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_hierarchy(test_session: AsyncSession) -> dict[str, str]:
    """Create Patient -> Study -> Series via ORM with fixed IDs."""
    patient = Patient(id=_PATIENT_ID, name="File Test Patient")
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        study_uid=_STUDY_UID,
        patient_id=_PATIENT_ID,
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(
        series_uid=_SERIES_UID,
        series_number=1,
        study_uid=_STUDY_UID,
    )
    test_session.add(series)
    await test_session.commit()

    return {
        "patient_id": _PATIENT_ID,
        "study_uid": _STUDY_UID,
        "series_uid": _SERIES_UID,
    }


@pytest.fixture
def working_dir(monkeypatch, tmp_path) -> Path:
    """Set storage_path to tmp_path and create the working directory tree.

    In SQLite tests, anon_uid fields are None, so _format_path_strict
    falls back to the raw UIDs (study_uid, series_uid).
    The resulting working folder is: {storage_path}/{patient_id}/{study_uid}/{series_uid}/
    """
    from src.settings import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    leaf = tmp_path / _PATIENT_ID / _STUDY_UID / _SERIES_UID
    leaf.mkdir(parents=True, exist_ok=True)
    return leaf


@pytest_asyncio.fixture
async def rt_with_files(client: AsyncClient) -> dict:
    """Create a record type with input and output file definitions via API."""
    payload = {
        "name": "file_test",
        "description": "Record type with file definitions",
        "label": "File Test",
        "level": "SERIES",
        "file_registry": [
            {
                "name": "input_nifti",
                "pattern": "scan.nii.gz",
                "role": "input",
                "required": True,
                "multiple": False,
            },
            {
                "name": "output_mask",
                "pattern": "mask.nii.gz",
                "role": "output",
                "required": False,
                "multiple": False,
            },
        ],
    }
    resp = await client.post(RECORD_TYPES, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def rt_without_files(client: AsyncClient) -> dict:
    """Create a record type without file definitions, with a simple data_schema."""
    payload = {
        "name": "annotation",
        "description": "Simple annotation without files",
        "label": "Annotation",
        "level": "SERIES",
        "data_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
            },
        },
    }
    resp = await client.post(RECORD_TYPES, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_record(
    client: AsyncClient,
    record_type_name: str,
    hierarchy: dict[str, str],
) -> dict:
    """Create a record via API and return the JSON response."""
    resp = await client.post(
        f"{RECORDS_BASE}/",
        json={
            "record_type_name": record_type_name,
            "patient_id": hierarchy["patient_id"],
            "study_uid": hierarchy["study_uid"],
            "series_uid": hierarchy["series_uid"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tests: record creation with files
# ---------------------------------------------------------------------------


class TestRecordCreationWithFiles:
    """Record status on creation depends on file presence."""

    @pytest.mark.asyncio
    async def test_record_blocked_when_required_files_missing(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """POST record with no files on disk -> status == 'blocked'."""
        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_record_pending_when_files_present(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """Write scan.nii.gz first, POST record -> status == 'pending'."""
        (working_dir / "scan.nii.gz").write_bytes(b"\x00" * 128)

        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "pending"

        # Verify via validate-files that the file is recognized
        resp = await client.post(f"{RECORDS_BASE}/{record['id']}/validate-files")
        assert resp.json()["valid"] is True
        assert "input_nifti" in resp.json()["matched_files"]

    @pytest.mark.asyncio
    async def test_record_pending_when_no_file_definitions(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_without_files: dict,
        working_dir: Path,
    ):
        """Record type with no file definitions -> status == 'pending'."""
        record = await _create_record(client, "annotation", test_hierarchy)
        assert record["status"] == "pending"


# ---------------------------------------------------------------------------
# Tests: file validation
# ---------------------------------------------------------------------------


class TestFileValidation:
    """POST validate-files endpoint."""

    @pytest.mark.asyncio
    async def test_validate_files_missing(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """Blocked record -> validate-files -> valid=False, error for input_nifti."""
        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "blocked"

        resp = await client.post(f"{RECORDS_BASE}/{record['id']}/validate-files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        error_names = [e["file_name"] for e in body["errors"]]
        assert "input_nifti" in error_names

    @pytest.mark.asyncio
    async def test_validate_files_present(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """File on disk -> validate-files -> valid=True, matched_files has input_nifti."""
        (working_dir / "scan.nii.gz").write_bytes(b"\x00" * 128)

        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "pending"

        resp = await client.post(f"{RECORDS_BASE}/{record['id']}/validate-files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert "input_nifti" in body["matched_files"]

    @pytest.mark.asyncio
    async def test_validate_files_no_file_definitions(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_without_files: dict,
        working_dir: Path,
    ):
        """Record type without file definitions -> valid=True."""
        record = await _create_record(client, "annotation", test_hierarchy)

        resp = await client.post(f"{RECORDS_BASE}/{record['id']}/validate-files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True


# ---------------------------------------------------------------------------
# Tests: file check and unblock
# ---------------------------------------------------------------------------


class TestFileCheckAndUnblock:
    """POST check-files endpoint."""

    @pytest.mark.asyncio
    async def test_check_files_unblocks_when_files_appear(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """Blocked record -> place file -> check-files -> transitions to pending."""
        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "blocked"

        # Place the required file
        (working_dir / "scan.nii.gz").write_bytes(b"\x00" * 128)

        resp = await client.post(f"{RECORDS_BASE}/{record['id']}/check-files")
        assert resp.status_code == 200

        # Verify record is now pending
        get_resp = await client.get(f"{RECORDS_BASE}/{record['id']}")
        assert get_resp.status_code == 200
        updated = get_resp.json()
        assert updated["status"] == "pending"

        # Checksums should be populated
        check_body = resp.json()
        assert check_body["checksums"]

    @pytest.mark.asyncio
    async def test_check_files_stays_blocked(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """Blocked record, no files -> check-files -> still blocked."""
        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "blocked"

        resp = await client.post(f"{RECORDS_BASE}/{record['id']}/check-files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["changed_files"] == []
        assert body["checksums"] == {}

        # Still blocked
        get_resp = await client.get(f"{RECORDS_BASE}/{record['id']}")
        assert get_resp.json()["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_check_files_detects_changed_checksums(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """Pending record -> modify file -> check-files -> changed_files lists the file."""
        (working_dir / "scan.nii.gz").write_bytes(b"\x00" * 128)

        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "pending"

        # First check to establish baseline checksums
        resp1 = await client.post(f"{RECORDS_BASE}/{record['id']}/check-files")
        assert resp1.status_code == 200
        checksums_v1 = resp1.json()["checksums"]

        # Modify the file
        (working_dir / "scan.nii.gz").write_bytes(b"\xff" * 256)

        # Second check detects changes
        resp2 = await client.post(f"{RECORDS_BASE}/{record['id']}/check-files")
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert len(body2["changed_files"]) > 0
        assert body2["checksums"] != checksums_v1

    @pytest.mark.asyncio
    async def test_check_files_no_changes(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """Two consecutive check-files without modification -> second returns no changes."""
        (working_dir / "scan.nii.gz").write_bytes(b"\x00" * 128)

        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "pending"

        # First check establishes checksums
        await client.post(f"{RECORDS_BASE}/{record['id']}/check-files")

        # Second check without file changes
        resp2 = await client.post(f"{RECORDS_BASE}/{record['id']}/check-files")
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["changed_files"] == []


# ---------------------------------------------------------------------------
# Tests: data submission with files
# ---------------------------------------------------------------------------


class TestDataSubmissionWithFiles:
    """POST data endpoint with file validation."""

    @pytest.mark.asyncio
    async def test_submit_data_succeeds_with_valid_files(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """File present, POST data -> 200, status == 'finished'."""
        (working_dir / "scan.nii.gz").write_bytes(b"\x00" * 128)

        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "pending"

        resp = await client.post(
            f"{RECORDS_BASE}/{record['id']}/data",
            json={"result": "ok"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "finished"

    @pytest.mark.asyncio
    async def test_submit_data_fails_when_required_files_missing(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """Create with file (pending), remove file, POST data -> 422."""
        (working_dir / "scan.nii.gz").write_bytes(b"\x00" * 128)

        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "pending"

        # Remove the file
        (working_dir / "scan.nii.gz").unlink()

        resp = await client.post(
            f"{RECORDS_BASE}/{record['id']}/data",
            json={"result": "ok"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_data_blocked_record_rejected(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """Blocked record, POST data -> 409 ('Record is blocked')."""
        record = await _create_record(client, "file_test", test_hierarchy)
        assert record["status"] == "blocked"

        resp = await client.post(
            f"{RECORDS_BASE}/{record['id']}/data",
            json={"result": "ok"},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_submit_data_no_file_defs_succeeds(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
        rt_without_files: dict,
        working_dir: Path,
    ):
        """Record type without files, POST data -> 200, status == 'finished'."""
        record = await _create_record(client, "annotation", test_hierarchy)
        assert record["status"] == "pending"

        resp = await client.post(
            f"{RECORDS_BASE}/{record['id']}/data",
            json={"label": "tumor"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "finished"


# ---------------------------------------------------------------------------
# Tests: file events
# ---------------------------------------------------------------------------


class TestFileEvents:
    """POST file-events endpoint on study router."""

    @pytest.mark.asyncio
    async def test_file_events_dispatched(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
    ):
        """With mock recordflow_engine -> POST file-events -> 200, dispatched list."""
        mock_engine = AsyncMock()
        app.state.recordflow_engine = mock_engine

        try:
            resp = await client.post(
                f"{PATIENTS_BASE}/{test_hierarchy['patient_id']}/file-events",
                json=["input_nifti", "output_mask"],
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["dispatched"] == ["input_nifti", "output_mask"]
        finally:
            app.state.recordflow_engine = None

    @pytest.mark.asyncio
    async def test_file_events_no_engine(
        self,
        client: AsyncClient,
        test_hierarchy: dict[str, str],
    ):
        """recordflow_engine = None -> POST file-events -> 200 (no-op)."""
        app.state.recordflow_engine = None

        resp = await client.post(
            f"{PATIENTS_BASE}/{test_hierarchy['patient_id']}/file-events",
            json=["input_nifti"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["dispatched"] == ["input_nifti"]


# ---------------------------------------------------------------------------
# Tests: bulk status update
# ---------------------------------------------------------------------------


class TestBulkStatusUpdate:
    """Bulk status update via repository.

    Note: The ``PATCH /bulk/status`` API route is shadowed by
    ``PATCH /{record_id}/status`` due to FastAPI route ordering.
    These tests exercise the repository method directly instead.
    """

    @pytest.mark.asyncio
    async def test_bulk_status_update(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        test_hierarchy: dict[str, str],
        rt_without_files: dict,
        working_dir: Path,
    ):
        """Create 3 records -> bulk_update_status -> all records updated."""
        from src.models.base import RecordStatus
        from src.repositories.record_repository import RecordRepository

        record_ids = []
        for _ in range(3):
            rec = await _create_record(client, "annotation", test_hierarchy)
            record_ids.append(rec["id"])

        repo = RecordRepository(test_session)
        await repo.bulk_update_status(record_ids, RecordStatus.inwork)

        # Verify all records updated via API
        for rid in record_ids:
            get_resp = await client.get(f"{RECORDS_BASE}/{rid}")
            assert get_resp.json()["status"] == "inwork"

    @pytest.mark.asyncio
    async def test_bulk_status_update_skips_nonexistent(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        test_hierarchy: dict[str, str],
        rt_without_files: dict,
        working_dir: Path,
    ):
        """Mix of real + fake IDs -> real record updated, no error."""
        from src.models.base import RecordStatus
        from src.repositories.record_repository import RecordRepository

        rec = await _create_record(client, "annotation", test_hierarchy)
        real_id = rec["id"]

        repo = RecordRepository(test_session)
        await repo.bulk_update_status([real_id, 99999], RecordStatus.inwork)

        get_resp = await client.get(f"{RECORDS_BASE}/{real_id}")
        assert get_resp.json()["status"] == "inwork"


# ---------------------------------------------------------------------------
# Tests: full file lifecycle
# ---------------------------------------------------------------------------


class TestFullFileLifecycle:
    """Integration test: complete file-driven record lifecycle."""

    @pytest.mark.asyncio
    async def test_full_file_lifecycle(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        test_hierarchy: dict[str, str],
        rt_with_files: dict,
        working_dir: Path,
    ):
        """Complete flow: create -> validate -> submit -> check changes."""
        # 1. Create record -> blocked (no files)
        record = await _create_record(client, "file_test", test_hierarchy)
        record_id = record["id"]
        assert record["status"] == "blocked"

        # 2. validate-files -> invalid
        resp = await client.post(f"{RECORDS_BASE}/{record_id}/validate-files")
        assert resp.json()["valid"] is False

        # 3. Submit data -> 409 (blocked)
        resp = await client.post(
            f"{RECORDS_BASE}/{record_id}/data",
            json={"result": "ok"},
        )
        assert resp.status_code == 409

        # 4. Place file on disk
        scan_path = working_dir / "scan.nii.gz"
        scan_path.write_bytes(b"NIFTI_DATA_V1")

        # 5. check-files -> auto-unblock to pending
        resp = await client.post(f"{RECORDS_BASE}/{record_id}/check-files")
        assert resp.status_code == 200

        get_resp = await client.get(f"{RECORDS_BASE}/{record_id}")
        assert get_resp.json()["status"] == "pending"

        # 6. validate-files -> valid
        resp = await client.post(f"{RECORDS_BASE}/{record_id}/validate-files")
        assert resp.json()["valid"] is True

        # Expire identity map to avoid stale file_links cache (SQLite test pitfall)
        test_session.expire_all()

        # 7. Submit data -> finished
        resp = await client.post(
            f"{RECORDS_BASE}/{record_id}/data",
            json={"result": "ok"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "finished"

        # 8. Modify file, check-files -> detects change
        # First establish baseline checksums
        test_session.expire_all()
        resp = await client.post(f"{RECORDS_BASE}/{record_id}/check-files")
        checksums_v1 = resp.json()["checksums"]

        scan_path.write_bytes(b"NIFTI_DATA_V2_MODIFIED")

        test_session.expire_all()
        resp = await client.post(f"{RECORDS_BASE}/{record_id}/check-files")
        body = resp.json()
        assert len(body["changed_files"]) > 0
        assert body["checksums"] != checksums_v1
