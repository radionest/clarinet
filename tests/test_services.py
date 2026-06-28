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
from clarinet.models.record import RecordTypeCreate, RecordTypeOptional
from clarinet.models.study import Study
from clarinet.models.user import User, UserCreate, UserRole, UserRolesLink, UserUpdate
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
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    make_user,
    seed_record,
)

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
        user = await env["service"].create_user(
            UserCreate(email="new@test.com", password="securepass")
        )
        assert user.email == "new@test.com"
        assert verify_password("securepass", user.hashed_password)
        assert user.is_active is True
        assert user.is_superuser is False
        assert user.is_verified is False

    @pytest.mark.asyncio
    async def test_create_user_duplicate_email(self, env):
        await env["service"].create_user(UserCreate(email="dup@test.com", password="securepass"))
        with pytest.raises(UserAlreadyExistsError):
            await env["service"].create_user(
                UserCreate(email="dup@test.com", password="securepass")
            )

    @pytest.mark.asyncio
    async def test_authenticate(self, env):
        await env["service"].create_user(UserCreate(email="auth@test.com", password="securepass"))
        user = await env["service"].authenticate("auth@test.com", "securepass")
        assert user.email == "auth@test.com"

    @pytest.mark.asyncio
    async def test_authenticate_invalid_password(self, env):
        await env["service"].create_user(
            UserCreate(email="authfail@test.com", password="securepass")
        )
        with pytest.raises(InvalidCredentialsError):
            await env["service"].authenticate("authfail@test.com", "wrongpassword")

    @pytest.mark.asyncio
    async def test_update_user_with_password(self, env):
        user = await env["service"].create_user(
            UserCreate(email="upd@test.com", password="oldpassw")
        )
        updated = await env["service"].update_user(user.id, UserUpdate(password="newpasswd"))
        assert verify_password("newpasswd", updated.hashed_password)

    @pytest.mark.asyncio
    async def test_update_user_password_none_keeps_old(self, env):
        user = await env["service"].create_user(
            UserCreate(email="nullpw@test.com", password="oldpassw")
        )
        updated = await env["service"].update_user(user.id, UserUpdate(password=None))
        assert verify_password("oldpassw", updated.hashed_password)

    @pytest.mark.asyncio
    async def test_update_user_without_password(self, env):
        user = await env["service"].create_user(
            UserCreate(email="nopw@test.com", password="keepthis")
        )
        updated = await env["service"].update_user(user.id, UserUpdate(is_active=False))
        assert updated.is_active is False
        assert verify_password("keepthis", updated.hashed_password)

    @pytest.mark.asyncio
    async def test_assign_role(self, env):
        user = await env["service"].create_user(
            UserCreate(email="role@test.com", password="pass1234")
        )
        updated = await env["service"].assign_role(user.id, "reviewer")
        assert await env["service"].user_repo.has_role(updated, "reviewer")

    @pytest.mark.asyncio
    async def test_assign_role_already_has(self, env):
        user = await env["service"].create_user(
            UserCreate(email="role2@test.com", password="pass1234")
        )
        await env["service"].assign_role(user.id, "reviewer")
        with pytest.raises(UserAlreadyHasRoleError):
            await env["service"].assign_role(user.id, "reviewer")

    @pytest.mark.asyncio
    async def test_remove_role(self, env):
        user = await env["service"].create_user(
            UserCreate(email="rmrole@test.com", password="pass1234")
        )
        await env["service"].assign_role(user.id, "reviewer")
        updated = await env["service"].remove_role(user.id, "reviewer")
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
        user = await env["service"].create_user(
            UserCreate(email="act@test.com", password="pass1234")
        )
        user.is_active = False
        await env["session"].commit()
        activated = await env["service"].activate_user(user.id)
        assert activated.is_active is True

    @pytest.mark.asyncio
    async def test_deactivate_user(self, env):
        user = await env["service"].create_user(
            UserCreate(email="deact@test.com", password="pass1234")
        )
        deactivated = await env["service"].deactivate_user(user.id)
        assert deactivated.is_active is False

    @pytest.mark.asyncio
    async def test_list_users(self, env):
        await env["service"].create_user(UserCreate(email="list@test.com", password="pass1234"))
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
        pat = make_patient("ADMIN_PAT", "Admin Patient")
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
    async def test_get_stats_workload_by_user(self, env):
        session = env["session"]
        pat = make_patient("WL_PAT", "Workload Patient")
        session.add(pat)
        await session.commit()
        study = make_study("WL_PAT", "1.2.3.700")
        session.add(study)
        await session.commit()
        series = make_series("1.2.3.700", "1.2.3.700.1", 1)
        session.add(series)
        await session.commit()
        rt = make_record_type("wl-rt")
        session.add(rt)
        await session.commit()

        u_busy = make_user(email="wl_busy@test.com", is_active=True)
        u_idle = make_user(email="wl_idle@test.com", is_active=True)
        u_off = make_user(email="wl_off@test.com", is_active=False)
        session.add_all([u_busy, u_idle, u_off])
        await session.commit()
        for u in (u_busy, u_idle, u_off):
            await session.refresh(u)

        async def add(user_id, status):
            await seed_record(
                session,
                patient_id="WL_PAT",
                study_uid="1.2.3.700",
                series_uid="1.2.3.700.1",
                rt_name="wl-rt",
                user_id=user_id,
                status=status,
            )

        await add(u_busy.id, RecordStatus.inwork)
        await add(u_busy.id, RecordStatus.failed)
        await add(u_off.id, RecordStatus.inwork)  # inactive user → excluded
        await add(None, RecordStatus.pending)  # available pool
        await add(None, RecordStatus.pending)  # available pool

        stats = await env["service"].get_stats()

        assert stats.available_pending == 2
        by_email = {w.email: w for w in stats.workload_by_user}
        assert "wl_busy@test.com" in by_email
        assert "wl_idle@test.com" in by_email  # active, zero-count row present
        assert "wl_off@test.com" not in by_email  # deactivated excluded
        assert by_email["wl_busy@test.com"].inwork == 1
        assert by_email["wl_busy@test.com"].failed == 1
        assert by_email["wl_busy@test.com"].pending == 0
        assert by_email["wl_idle@test.com"].inwork == 0

    @pytest.mark.asyncio
    async def test_get_stats_finished_and_available(self, env):
        session = env["session"]
        pat = make_patient("FA_PAT", "Finished/Available Patient")
        session.add(pat)
        await session.commit()
        study = make_study("FA_PAT", "1.2.3.800")
        session.add(study)
        await session.commit()
        series = make_series("1.2.3.800", "1.2.3.800.1", 1)
        session.add(series)
        await session.commit()

        # Two roles, each backing its own record type.
        session.add_all([UserRole(name="fa-role"), UserRole(name="other-role")])
        await session.commit()
        session.add_all(
            [
                make_record_type("fa-rt", role_name="fa-role"),
                make_record_type("other-rt", role_name="other-role"),
            ]
        )
        await session.commit()

        # Regular user holds only fa-role; superuser holds no role.
        u_role = make_user(email="fa_role@test.com", is_active=True)
        u_super = make_user(email="fa_super@test.com", is_active=True, is_superuser=True)
        session.add_all([u_role, u_super])
        await session.commit()
        session.add(UserRolesLink(user_id=u_role.id, role_name="fa-role"))
        await session.commit()

        async def add(rt_name, user_id, status):
            await seed_record(
                session,
                patient_id="FA_PAT",
                study_uid="1.2.3.800",
                series_uid="1.2.3.800.1",
                rt_name=rt_name,
                user_id=user_id,
                status=status,
            )

        # Claimable pool: 3 fa-role + 2 other-role, unassigned + pending.
        for _ in range(3):
            await add("fa-rt", None, RecordStatus.pending)
        for _ in range(2):
            await add("other-rt", None, RecordStatus.pending)
        # One finished record assigned to the regular user.
        await add("fa-rt", u_role.id, RecordStatus.finished)

        stats = await env["service"].get_stats()
        by_email = {w.email: w for w in stats.workload_by_user}

        assert by_email["fa_role@test.com"].finished == 1
        assert by_email["fa_role@test.com"].available == 3  # fa-role pool only
        assert by_email["fa_super@test.com"].available == 5  # whole pool
        assert by_email["fa_super@test.com"].finished == 0

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
    async def test_load_flows_syntax_error_raises(self, tmp_path):
        from clarinet.exceptions.domain import ConfigLoadError
        from clarinet.services.recordflow.flow_loader import load_flows_from_file

        flow_file = tmp_path / "bad_flow.py"
        flow_file.write_text("def broken(\n")
        with pytest.raises(ConfigLoadError):
            load_flows_from_file(flow_file)

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

    @pytest.mark.asyncio
    async def test_cross_flow_import_no_reexecution(self, tmp_path):
        """A flow file importing a sibling flow file reuses the cached module —
        re-execution would register the sibling's flows a second time (count
        would be 3 instead of 2)."""
        from unittest.mock import MagicMock

        from clarinet.services.recordflow.flow_loader import load_and_register_flows

        (tmp_path / "alpha_cross_flow.py").write_text(
            "from clarinet.services.recordflow import record\n"
            "SHARED = 1\n"
            "record('cross-a').on_status('finished').add_record('cross-a2')\n"
        )
        (tmp_path / "beta_cross_flow.py").write_text(
            "from clarinet_plan.alpha_cross_flow import SHARED\n"
            "from clarinet.services.recordflow import record\n"
            "record('cross-b').on_status('finished').add_record('cross-b2')\n"
        )

        engine = MagicMock()
        count = load_and_register_flows(
            engine,
            [tmp_path / "alpha_cross_flow.py", tmp_path / "beta_cross_flow.py"],
        )
        assert count == 2

    @pytest.mark.asyncio
    async def test_cross_flow_import_reverse_direction(self, tmp_path):
        """Mirror of the above: an EARLIER-sorted file importing a LATER one now
        works too — the native module cache removes the old sorted-order limit."""
        from unittest.mock import MagicMock

        from clarinet.services.recordflow.flow_loader import load_and_register_flows

        # a_flow sorts before z_flow yet imports it.
        (tmp_path / "a_early_flow.py").write_text(
            "from clarinet_plan.z_late_flow import SHARED\n"
            "from clarinet.services.recordflow import record\n"
            "record('rev-a').on_status('finished').add_record('rev-a2')\n"
        )
        (tmp_path / "z_late_flow.py").write_text(
            "from clarinet.services.recordflow import record\n"
            "SHARED = 9\n"
            "record('rev-z').on_status('finished').add_record('rev-z2')\n"
        )

        engine = MagicMock()
        count = load_and_register_flows(
            engine,
            [tmp_path / "a_early_flow.py", tmp_path / "z_late_flow.py"],
        )
        assert count == 2

    @pytest.mark.asyncio
    async def test_call_callbacks_survive_across_files(self, tmp_path):
        """Two flow files each with ``.call()`` — both callbacks remain in the
        call registry. The old per-file ``call_function_registry.reset()`` erased
        earlier files' callbacks (latent multi-file bug); now cleared once per
        cycle."""
        from unittest.mock import MagicMock

        from clarinet.services.recordflow import call_function_registry
        from clarinet.services.recordflow.flow_loader import load_and_register_flows

        (tmp_path / "a_call_flow.py").write_text(
            "from clarinet.services.recordflow import record\n"
            "async def cb_a(record, context, client):\n"
            "    return None\n"
            "record('ca').on_finished().call(cb_a)\n"
        )
        (tmp_path / "b_call_flow.py").write_text(
            "from clarinet.services.recordflow import record\n"
            "async def cb_b(record, context, client):\n"
            "    return None\n"
            "record('cb').on_finished().call(cb_b)\n"
        )

        engine = MagicMock()
        load_and_register_flows(engine, [tmp_path / "a_call_flow.py", tmp_path / "b_call_flow.py"])

        ids = call_function_registry.all_ids()
        assert any(i.endswith(".cb_a") for i in ids), ids
        assert any(i.endswith(".cb_b") for i in ids), ids

    @pytest.mark.asyncio
    async def test_load_and_register_flows_aggregates_failures(self, tmp_path):
        """Every broken flow file is reported in ONE error (no fix-restart-fix
        cycles), and when any file fails NOTHING is registered with the engine —
        a broken file's partially-registered flows must not leak through."""
        from unittest.mock import MagicMock

        from clarinet.exceptions.domain import ConfigLoadError
        from clarinet.services.recordflow.flow_loader import load_and_register_flows

        good_file = tmp_path / "a_good_flow.py"
        good_file.write_text(
            "from clarinet.services.recordflow import record\n"
            "record('test-agg-1').on_status('finished').add_record('test-agg-2')\n"
        )
        broken_one = tmp_path / "b_broken_flow.py"
        broken_one.write_text("raise RuntimeError('first failure')\n")
        broken_two = tmp_path / "c_broken_flow.py"
        broken_two.write_text("raise RuntimeError('second failure')\n")

        engine = MagicMock()
        with pytest.raises(ConfigLoadError, match="2 flow file"):
            load_and_register_flows(engine, [good_file, broken_one, broken_two])

        # Aggregate raises before registration — the good file's flows are not
        # registered when the batch contains failures.
        assert not engine.register_flow.called


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
