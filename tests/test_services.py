"""Integration tests for service layer — real SQLite, no mocks."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from clarinet.exceptions.domain import (
    InvalidCredentialsError,
    RoleAlreadyExistsError,
    UserAlreadyExistsError,
    UserAlreadyHasRoleError,
    ValidationError,
)
from clarinet.models.base import RecordStatus
from clarinet.models.patient import Patient
from clarinet.models.record import RecordTypeCreate, RecordTypeOptional
from clarinet.models.study import Study
from clarinet.models.user import User, UserRole
from clarinet.repositories.file_definition_repository import FileDefinitionRepository
from clarinet.repositories.patient_repository import PatientRepository
from clarinet.repositories.record_repository import RecordRepository
from clarinet.repositories.record_type_repository import RecordTypeRepository
from clarinet.repositories.study_repository import StudyRepository
from clarinet.repositories.user_repository import UserRepository
from clarinet.services.admin_service import AdminService
from clarinet.services.record_type_service import RecordTypeService
from clarinet.services.user_service import UserService
from clarinet.utils.auth import get_password_hash, verify_password

# ===================================================================
# UserService
# ===================================================================


class TestUserService:
    """Tests for UserService."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        user_repo = UserRepository(test_session)
        service = UserService(user_repo)
        # Create a role for testing
        role = UserRole(name="reviewer")
        test_session.add(role)
        await test_session.commit()
        return {"service": service, "session": test_session, "role": role}

    @pytest.mark.asyncio
    async def test_create_user(self, env):
        uid = uuid4()
        user = await env["service"].create_user(
            {"id": uid, "email": "new@test.com", "password": "securepass"}
        )
        assert user.email == "new@test.com"
        assert verify_password("securepass", user.hashed_password)

    @pytest.mark.asyncio
    async def test_create_user_duplicate(self, env):
        uid = uuid4()
        await env["service"].create_user(
            {"id": uid, "email": "dup@test.com", "password": "securepass"}
        )
        with pytest.raises(UserAlreadyExistsError):
            await env["service"].create_user(
                {"id": uid, "email": "dup2@test.com", "password": "securepass"}
            )

    @pytest.mark.asyncio
    async def test_authenticate(self, env):
        uid = uuid4()
        await env["service"].create_user(
            {"id": uid, "email": "auth@test.com", "password": "securepass"}
        )
        user = await env["service"].authenticate("auth@test.com", "securepass")
        assert user.email == "auth@test.com"

    @pytest.mark.asyncio
    async def test_authenticate_invalid_password(self, env):
        uid = uuid4()
        await env["service"].create_user(
            {"id": uid, "email": "authfail@test.com", "password": "securepass"}
        )
        with pytest.raises(InvalidCredentialsError):
            await env["service"].authenticate("authfail@test.com", "wrongpassword")

    @pytest.mark.asyncio
    async def test_update_user_with_password(self, env):
        uid = uuid4()
        await env["service"].create_user(
            {"id": uid, "email": "upd@test.com", "password": "oldpass"}
        )
        updated = await env["service"].update_user(uid, {"password": "newpass"})
        assert verify_password("newpass", updated.hashed_password)

    @pytest.mark.asyncio
    async def test_assign_role(self, env):
        uid = uuid4()
        await env["service"].create_user(
            {"id": uid, "email": "role@test.com", "password": "pass1234"}
        )
        updated = await env["service"].assign_role(uid, "reviewer")
        # Verify role was assigned via has_role
        assert await env["service"].user_repo.has_role(updated, "reviewer")

    @pytest.mark.asyncio
    async def test_assign_role_already_has(self, env):
        uid = uuid4()
        await env["service"].create_user(
            {"id": uid, "email": "role2@test.com", "password": "pass1234"}
        )
        await env["service"].assign_role(uid, "reviewer")
        with pytest.raises(UserAlreadyHasRoleError):
            await env["service"].assign_role(uid, "reviewer")

    @pytest.mark.asyncio
    async def test_remove_role(self, env):
        uid = uuid4()
        await env["service"].create_user(
            {"id": uid, "email": "rmrole@test.com", "password": "pass1234"}
        )
        await env["service"].assign_role(uid, "reviewer")
        updated = await env["service"].remove_role(uid, "reviewer")
        assert not await env["service"].user_repo.has_role(updated, "reviewer")

    @pytest.mark.asyncio
    async def test_create_role(self, env):
        role = await env["service"].create_role("newrole")
        assert role.name == "newrole"

    @pytest.mark.asyncio
    async def test_create_role_duplicate(self, env):
        await env["service"].create_role("duprole")
        with pytest.raises(RoleAlreadyExistsError):
            await env["service"].create_role("duprole")

    @pytest.mark.asyncio
    async def test_activate_user(self, env):
        uid = uuid4()
        user = await env["service"].create_user(
            {"id": uid, "email": "act@test.com", "password": "pass1234"}
        )
        user.is_active = False
        await env["session"].commit()
        activated = await env["service"].activate_user(uid)
        assert activated.is_active is True

    @pytest.mark.asyncio
    async def test_deactivate_user(self, env):
        uid = uuid4()
        await env["service"].create_user(
            {"id": uid, "email": "deact@test.com", "password": "pass1234"}
        )
        deactivated = await env["service"].deactivate_user(uid)
        assert deactivated.is_active is False

    @pytest.mark.asyncio
    async def test_list_users(self, env):
        uid = uuid4()
        await env["service"].create_user(
            {"id": uid, "email": "list@test.com", "password": "pass1234"}
        )
        users = await env["service"].list_users()
        assert len(users) >= 1


# ===================================================================
# AdminService
# ===================================================================


class TestAdminService:
    """Tests for AdminService."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        record_repo = RecordRepository(test_session)
        record_type_repo = RecordTypeRepository(test_session)
        study_repo = StudyRepository(test_session)
        patient_repo = PatientRepository(test_session)
        user_repo = UserRepository(test_session)
        service = AdminService(record_repo, record_type_repo, study_repo, patient_repo, user_repo)
        return {"service": service, "session": test_session}

    @pytest.mark.asyncio
    async def test_get_stats_empty(self, env):
        stats = await env["service"].get_stats()
        assert isinstance(stats.total_studies, int)
        assert isinstance(stats.total_records, int)
        assert isinstance(stats.total_users, int)
        assert isinstance(stats.total_patients, int)
        # All RecordStatus values should be present
        for status in RecordStatus:
            assert status.value in stats.records_by_status

    @pytest.mark.asyncio
    async def test_get_stats_with_data(self, env):
        session = env["session"]
        pat = Patient(id="ADMIN_PAT", name="Admin Patient")
        session.add(pat)
        await session.commit()
        study = Study(patient_id="ADMIN_PAT", study_uid="1.2.3.600", date=datetime.now(UTC).date())
        session.add(study)
        await session.commit()
        user = User(
            id=uuid4(),
            email="admin_s@test.com",
            hashed_password=get_password_hash("pass"),
            is_active=True,
        )
        session.add(user)
        await session.commit()

        stats = await env["service"].get_stats()
        assert stats.total_studies >= 1
        assert stats.total_patients >= 1
        assert stats.total_users >= 1

    @pytest.mark.asyncio
    async def test_get_record_type_stats_empty(self, env):
        result = await env["service"].get_record_type_stats()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_record_type_stats_with_data(self, env):
        from clarinet.models.record import RecordType

        rt = RecordType(name="test-stats-type", description="Test", label="TST")
        env["session"].add(rt)
        await env["session"].commit()

        result = await env["service"].get_record_type_stats()
        assert len(result) == 1

        stat = result[0]
        assert stat.name == "test-stats-type"
        assert stat.description == "Test"
        assert stat.label == "TST"
        assert stat.level == "SERIES"  # default DicomQueryLevel
        assert stat.role_name is None
        assert stat.min_records == 1
        assert stat.max_records is None
        assert stat.total_records == 0
        assert stat.unique_users == 0
        # All RecordStatus keys present in records_by_status with 0 counts
        status_counts = stat.records_by_status.model_dump()
        for status in RecordStatus:
            assert status_counts[status.value] == 0


# ===================================================================
# AnonymousNameProvider
# ===================================================================


class TestAnonymousNameProvider:
    """Tests for AnonymousNameProvider."""

    @pytest.mark.asyncio
    async def test_get_available_names(self, tmp_path):
        from clarinet.services.providers.anonymous_name_provider import AnonymousNameProvider

        names_file = tmp_path / "names.txt"
        names_file.write_text("Alice\nBob\nCharlie\n")

        provider = AnonymousNameProvider(str(names_file))
        names = await provider.get_available_names()
        assert names == ["Alice", "Bob", "Charlie"]

    @pytest.mark.asyncio
    async def test_get_available_names_cached(self, tmp_path):
        from clarinet.services.providers.anonymous_name_provider import AnonymousNameProvider

        names_file = tmp_path / "names.txt"
        names_file.write_text("Alice\nBob\n")

        provider = AnonymousNameProvider(str(names_file))
        first = await provider.get_available_names()
        # Modify file — cache should still return old data
        names_file.write_text("Diana\nEve\n")
        second = await provider.get_available_names()
        assert first == second

    @pytest.mark.asyncio
    async def test_clear_cache(self, tmp_path):
        from clarinet.services.providers.anonymous_name_provider import AnonymousNameProvider

        names_file = tmp_path / "names.txt"
        names_file.write_text("Alice\n")

        provider = AnonymousNameProvider(str(names_file))
        await provider.get_available_names()
        provider.clear_cache()
        assert provider._names_cache is None
        # Next call reloads
        names = await provider.get_available_names()
        assert names == ["Alice"]

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        from clarinet.services.providers.anonymous_name_provider import AnonymousNameProvider

        provider = AnonymousNameProvider(str(tmp_path / "nonexistent.txt"))
        names = await provider.get_available_names()
        assert names == []

    @pytest.mark.asyncio
    async def test_no_file_path(self):
        from clarinet.services.providers.anonymous_name_provider import AnonymousNameProvider

        provider = AnonymousNameProvider(None)
        names = await provider.get_available_names()
        assert names == []


# ===================================================================
# FlowLoader
# ===================================================================


class TestFlowLoader:
    """Tests for FlowLoader functions."""

    @pytest.mark.asyncio
    async def test_load_flows_from_file(self, tmp_path):
        from clarinet.services.recordflow.flow_loader import load_flows_from_file

        flow_file = tmp_path / "test_flow.py"
        flow_file.write_text(
            "from clarinet.services.recordflow import record\n"
            "record('test-record-1').on_status('finished').add_record('test-record-2')\n"
        )
        flows = load_flows_from_file(flow_file)
        assert len(flows) >= 1

    @pytest.mark.asyncio
    async def test_load_flows_syntax_error(self, tmp_path):
        from clarinet.services.recordflow.flow_loader import load_flows_from_file

        flow_file = tmp_path / "bad_flow.py"
        flow_file.write_text("def broken(\n")
        flows = load_flows_from_file(flow_file)
        assert flows == []

    @pytest.mark.asyncio
    async def test_load_flows_file_not_found(self, tmp_path):
        from clarinet.services.recordflow.flow_loader import load_flows_from_file

        flows = load_flows_from_file(tmp_path / "nonexistent.py")
        assert flows == []

    @pytest.mark.asyncio
    async def test_find_flow_files(self, tmp_path):
        from clarinet.services.recordflow.flow_loader import find_flow_files

        (tmp_path / "a_flow.py").write_text("# flow")
        (tmp_path / "b_flow.py").write_text("# flow")
        (tmp_path / "not_a_flow.txt").write_text("# not flow")
        files = find_flow_files(tmp_path)
        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_find_flow_files_empty_dir(self, tmp_path):
        from clarinet.services.recordflow.flow_loader import find_flow_files

        files = find_flow_files(tmp_path)
        assert files == []

    @pytest.mark.asyncio
    async def test_load_and_register_flows(self, tmp_path):
        from unittest.mock import MagicMock

        from clarinet.services.recordflow.flow_loader import load_and_register_flows

        flow_file = tmp_path / "reg_flow.py"
        flow_file.write_text(
            "from clarinet.services.recordflow import record\n"
            "record('test-load-reg').on_status('finished').add_record('test-load-r2')\n"
        )

        engine = MagicMock()
        count = load_and_register_flows(engine, [flow_file])
        assert count >= 1
        assert engine.register_flow.called


# ===================================================================
# RecordTypeService
# ===================================================================


class TestRecordTypeService:
    """Tests for RecordTypeService."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        rt_repo = RecordTypeRepository(test_session)
        fd_repo = FileDefinitionRepository(test_session)
        service = RecordTypeService(rt_repo, fd_repo, test_session)
        return {"service": service, "session": test_session, "rt_repo": rt_repo}

    @pytest.mark.asyncio
    async def test_create_record_type(self, env):
        dto = RecordTypeCreate(name="svc-rt-create", label="CRT")
        result = await env["service"].create_record_type(dto)
        assert result.name == "svc-rt-create"
        assert result.label == "CRT"

    @pytest.mark.asyncio
    async def test_create_record_type_invalid_schema(self, env):
        dto = RecordTypeCreate(
            name="svc-rt-bad-schema",
            data_schema={"type": "invalid_not_a_type"},
        )
        with pytest.raises(ValidationError, match="Data schema is invalid"):
            await env["service"].create_record_type(dto)

    @pytest.mark.asyncio
    async def test_update_record_type(self, env):
        dto = RecordTypeCreate(name="svc-rt-upd", description="Original")
        await env["service"].create_record_type(dto)

        update = RecordTypeOptional(description="Updated")
        update.model_fields_set.add("description")
        result = await env["service"].update_record_type("svc-rt-upd", update)
        assert result.description == "Updated"

    @pytest.mark.asyncio
    async def test_validate_record_data_valid(self, env):
        from unittest.mock import MagicMock

        schema = {
            "type": "object",
            "properties": {"score": {"type": "integer"}},
            "required": ["score"],
        }
        dto = RecordTypeCreate(name="svc-rt-valid", data_schema=schema)
        rt = await env["service"].create_record_type(dto)

        record = MagicMock()
        record.record_type = rt
        record.study_uid = None

        data = {"score": 42}
        result = await env["service"].validate_record_data(record, data)
        assert result == data

    @pytest.mark.asyncio
    async def test_validate_record_data_invalid(self, env):
        from unittest.mock import MagicMock

        schema = {
            "type": "object",
            "properties": {"score": {"type": "integer"}},
            "required": ["score"],
        }
        dto = RecordTypeCreate(name="svc-rt-invalid", data_schema=schema)
        rt = await env["service"].create_record_type(dto)

        record = MagicMock()
        record.record_type = rt
        record.study_uid = None

        with pytest.raises(ValidationError):
            await env["service"].validate_record_data(record, {"score": "not_int"})
