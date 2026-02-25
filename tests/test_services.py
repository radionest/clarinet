"""Integration tests for service layer — real SQLite, no mocks."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from src.exceptions.domain import (
    InvalidCredentialsError,
    RoleAlreadyExistsError,
    UserAlreadyExistsError,
    UserAlreadyHasRoleError,
)
from src.models.base import RecordStatus
from src.models.patient import Patient
from src.models.study import Study
from src.models.user import User, UserRole
from src.repositories.patient_repository import PatientRepository
from src.repositories.record_repository import RecordRepository
from src.repositories.record_type_repository import RecordTypeRepository
from src.repositories.study_repository import StudyRepository
from src.repositories.user_repository import UserRepository, UserRoleRepository
from src.services.admin_service import AdminService
from src.services.user_service import UserService
from src.utils.auth import get_password_hash, verify_password

# ===================================================================
# UserService
# ===================================================================


class TestUserService:
    """Tests for UserService."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        user_repo = UserRepository(test_session)
        role_repo = UserRoleRepository(test_session)
        service = UserService(user_repo, role_repo)
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
    async def test_get_total_counts_empty(self, env):
        studies, records, _users, _patients = await env["service"].get_total_counts()
        assert isinstance(studies, int)
        assert isinstance(records, int)

    @pytest.mark.asyncio
    async def test_get_total_counts_with_data(self, env):
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

        studies, _records, users, patients = await env["service"].get_total_counts()
        assert studies >= 1
        assert patients >= 1
        assert users >= 1

    @pytest.mark.asyncio
    async def test_get_records_by_status(self, env):
        status_map = await env["service"].get_records_by_status()
        # All RecordStatus values should be present
        for status in RecordStatus:
            assert status.value in status_map

    @pytest.mark.asyncio
    async def test_get_record_type_stats(self, env):
        record_types, status_map, user_map = await env["service"].get_record_type_stats()
        assert isinstance(record_types, list)
        assert isinstance(status_map, dict)
        assert isinstance(user_map, dict)


# ===================================================================
# AnonymousNameProvider
# ===================================================================


class TestAnonymousNameProvider:
    """Tests for AnonymousNameProvider."""

    @pytest.mark.asyncio
    async def test_get_available_names(self, tmp_path):
        from src.services.providers.anonymous_name_provider import AnonymousNameProvider

        names_file = tmp_path / "names.txt"
        names_file.write_text("Alice\nBob\nCharlie\n")

        provider = AnonymousNameProvider(str(names_file))
        names = await provider.get_available_names()
        assert names == ["Alice", "Bob", "Charlie"]

    @pytest.mark.asyncio
    async def test_get_available_names_cached(self, tmp_path):
        from src.services.providers.anonymous_name_provider import AnonymousNameProvider

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
        from src.services.providers.anonymous_name_provider import AnonymousNameProvider

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
        from src.services.providers.anonymous_name_provider import AnonymousNameProvider

        provider = AnonymousNameProvider(str(tmp_path / "nonexistent.txt"))
        names = await provider.get_available_names()
        assert names == []

    @pytest.mark.asyncio
    async def test_no_file_path(self):
        from src.services.providers.anonymous_name_provider import AnonymousNameProvider

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
        from src.services.recordflow.flow_loader import load_flows_from_file

        flow_file = tmp_path / "test_flow.py"
        flow_file.write_text(
            "record('test_record_1').on_status('finished').add_record('test_record_2')\n"
        )
        flows = load_flows_from_file(flow_file)
        assert len(flows) >= 1

    @pytest.mark.asyncio
    async def test_load_flows_syntax_error(self, tmp_path):
        from src.services.recordflow.flow_loader import load_flows_from_file

        flow_file = tmp_path / "bad_flow.py"
        flow_file.write_text("def broken(\n")
        flows = load_flows_from_file(flow_file)
        assert flows == []

    @pytest.mark.asyncio
    async def test_load_flows_file_not_found(self, tmp_path):
        from src.services.recordflow.flow_loader import load_flows_from_file

        flows = load_flows_from_file(tmp_path / "nonexistent.py")
        assert flows == []

    @pytest.mark.asyncio
    async def test_find_flow_files(self, tmp_path):
        from src.services.recordflow.flow_loader import find_flow_files

        (tmp_path / "a_flow.py").write_text("# flow")
        (tmp_path / "b_flow.py").write_text("# flow")
        (tmp_path / "not_a_flow.txt").write_text("# not flow")
        files = find_flow_files(tmp_path)
        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_find_flow_files_empty_dir(self, tmp_path):
        from src.services.recordflow.flow_loader import find_flow_files

        files = find_flow_files(tmp_path)
        assert files == []

    @pytest.mark.asyncio
    async def test_load_and_register_flows(self, tmp_path):
        from unittest.mock import MagicMock

        from src.services.recordflow.flow_loader import load_and_register_flows

        flow_file = tmp_path / "reg_flow.py"
        flow_file.write_text(
            "record('test_load_reg').on_status('finished').add_record('test_load_r2')\n"
        )

        engine = MagicMock()
        count = load_and_register_flows(engine, [flow_file])
        assert count >= 1
        assert engine.register_flow.called
