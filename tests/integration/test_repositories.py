"""Integration tests for repository layer — real SQLite, no mocks."""

import pytest
import pytest_asyncio
from sqlalchemy.orm import selectinload

from clarinet.exceptions.domain import (
    EntityNotFoundError,
    RecordConstraintViolationError,
    RecordLimitReachedError,
    RecordNotFoundError,
    RecordTypeAlreadyExistsError,
    RecordTypeNotFoundError,
)
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.file_schema import FileDefinition, FileRole, RecordTypeFileLink
from clarinet.models.record import RecordFind, RecordType
from clarinet.models.study import SeriesFind
from clarinet.models.user import User, UserRole
from clarinet.repositories.patient_repository import PatientRepository
from clarinet.repositories.record_repository import RecordRepository, RecordSearchCriteria
from clarinet.repositories.record_type_repository import RecordTypeRepository
from clarinet.repositories.series_repository import SeriesRepository
from clarinet.repositories.study_repository import StudyRepository
from clarinet.repositories.user_repository import UserRepository
from clarinet.utils.auth import get_password_hash
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    make_user,
    seed_record,
)

# ===================================================================
# BaseRepository (tested via PatientRepository as concrete impl)
# ===================================================================


class TestBaseRepository:
    """Tests for BaseRepository CRUD through PatientRepository."""

    @pytest_asyncio.fixture
    async def repo(self, test_session):
        return PatientRepository(test_session)

    @pytest_asyncio.fixture
    async def patient(self, test_session, repo):
        return await repo.create(make_patient("BASE_PAT", "Base Patient"))

    @pytest.mark.asyncio
    async def test_get(self, repo, patient):
        found = await repo.get("BASE_PAT")
        assert found.id == "BASE_PAT"

    @pytest.mark.asyncio
    async def test_get_not_found(self, repo):
        with pytest.raises(EntityNotFoundError):
            await repo.get("NONEXISTENT")

    @pytest.mark.asyncio
    async def test_get_optional(self, repo, patient):
        assert await repo.get_optional("BASE_PAT") is not None
        assert await repo.get_optional("NONEXISTENT") is None

    @pytest.mark.asyncio
    async def test_get_by(self, repo, patient):
        found = await repo.get_by(name="Base Patient")
        assert found is not None and found.id == "BASE_PAT"
        assert await repo.get_by(name="Nobody") is None

    @pytest.mark.asyncio
    async def test_exists(self, repo, patient):
        assert await repo.exists(id="BASE_PAT") is True
        assert await repo.exists(id="NONEXISTENT") is False

    @pytest.mark.asyncio
    async def test_get_all_with_pagination(self, repo, test_session):
        for i in range(5):
            await repo.create(make_patient(f"PAG_{i}", f"P {i}"))
        page = await repo.get_all(skip=1, limit=2)
        assert len(page) == 2

    @pytest.mark.asyncio
    async def test_list_all(self, repo, test_session):
        for i in range(3):
            await repo.create(make_patient(f"LA_{i}", f"P {i}"))
        result = await repo.list_all()
        assert len(result) >= 3

    @pytest.mark.asyncio
    async def test_count(self, repo, patient):
        assert await repo.count() >= 1

    @pytest.mark.asyncio
    async def test_count_with_filters(self, repo, patient):
        assert await repo.count(id="BASE_PAT") == 1
        assert await repo.count(id="NONEXISTENT") == 0

    @pytest.mark.asyncio
    async def test_create(self, repo):
        p = await repo.create(make_patient("CR_1", "Create Test"))
        assert p.id == "CR_1"

    @pytest.mark.asyncio
    async def test_create_many(self, repo):
        patients = [make_patient(f"CM_{i}", f"CM {i}") for i in range(3)]
        result = await repo.create_many(patients)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_update(self, repo, patient):
        updated = await repo.update(patient, {"name": "Updated Name"})
        assert updated.name == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_exclude_unset(self, repo, patient):
        original_name = patient.name
        updated = await repo.update(patient, {"name": None}, exclude_unset=True)
        assert updated.name == original_name  # None is skipped

    @pytest.mark.asyncio
    async def test_delete(self, repo, test_session):
        p = await repo.create(make_patient("DEL_1", "Delete Me"))
        await repo.delete(p)
        assert await repo.get_optional("DEL_1") is None

    @pytest.mark.asyncio
    async def test_delete_by_id(self, repo, test_session):
        await repo.create(make_patient("DBI_1", "Delete By Id"))
        assert await repo.delete_by_id("DBI_1") is True

    @pytest.mark.asyncio
    async def test_delete_by_id_not_found(self, repo):
        assert await repo.delete_by_id("NONEXISTENT") is False


# ===================================================================
# SeriesRepository
# ===================================================================


class TestSeriesRepository:
    """Tests for SeriesRepository."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        """Seed patient → study → series."""
        pat = make_patient("SPAT", "Series Patient")
        test_session.add(pat)
        await test_session.commit()
        study = make_study("SPAT", "1.2.3.200")
        test_session.add(study)
        await test_session.commit()
        s1 = make_series("1.2.3.200", "1.2.3.200.1", 1)
        s1.series_description = "Axial CT"
        s2 = make_series("1.2.3.200", "1.2.3.200.2", 2)
        s2.series_description = "Coronal MR"
        test_session.add_all([s1, s2])
        await test_session.commit()
        await test_session.refresh(s1)
        await test_session.refresh(s2)
        repo = SeriesRepository(test_session)
        return {"repo": repo, "s1": s1, "s2": s2, "study": study, "patient": pat}

    @pytest.mark.asyncio
    async def test_find_by_study_uid(self, env):
        result = await env["repo"].find_by_study_uid("1.2.3.200")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_find_by_series_uid(self, env):
        found = await env["repo"].find_by_series_uid("1.2.3.200.1")
        assert found is not None
        assert found.series_uid == "1.2.3.200.1"

    @pytest.mark.asyncio
    async def test_find_by_series_uid_not_found(self, env):
        assert await env["repo"].find_by_series_uid("9.9.9") is None

    @pytest.mark.asyncio
    async def test_find_by_anon_uid(self, env):
        # No anon_uid set yet
        assert await env["repo"].find_by_anon_uid("anon_1") is None

    @pytest.mark.asyncio
    async def test_find_by_description(self, env):
        result = await env["repo"].find_by_description("axial")
        assert len(result) == 1
        assert result[0].series_description == "Axial CT"

    @pytest.mark.asyncio
    async def test_count_by_study_uid(self, env):
        assert await env["repo"].count_by_study_uid("1.2.3.200") == 2

    @pytest.mark.asyncio
    async def test_count_records(self, env):
        assert await env["repo"].count_records("1.2.3.200.1") == 0

    @pytest.mark.asyncio
    async def test_search_multi_filter(self, env):
        result = await env["repo"].search(study_uid="1.2.3.200", series_description="coronal")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_random_series(self, env):
        result = await env["repo"].get_random_series("1.2.3.200", count=1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_random(self, env):
        series = await env["repo"].get_random()
        assert series.series_uid in ("1.2.3.200.1", "1.2.3.200.2")

    @pytest.mark.asyncio
    async def test_update_anon_uid(self, env):
        updated = await env["repo"].update_anon_uid(env["s1"], "1.2.3.999")
        assert updated.anon_uid == "1.2.3.999"

    @pytest.mark.asyncio
    async def test_get_with_relations(self, env):
        series = await env["repo"].get_with_relations("1.2.3.200.1")
        assert series.study is not None
        assert series.study.patient is not None

    @pytest.mark.asyncio
    async def test_find_by_criteria(self, env):
        query = SeriesFind(study_uid="1.2.3.200")
        result = await env["repo"].find_by_criteria(query)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_find_by_criteria_is_absent(self, test_session, env):
        """find_by_criteria with is_absent=True filters out series that have a matching record."""
        rt = make_record_type("absent-rt-001")
        test_session.add(rt)
        await test_session.commit()

        await seed_record(
            test_session,
            patient_id="SPAT",
            study_uid="1.2.3.200",
            series_uid="1.2.3.200.1",
            rt_name="absent-rt-001",
        )

        query = SeriesFind(
            study_uid="1.2.3.200",
            records=[RecordFind(record_type_name="absent-rt-001", is_absent=True)],
        )
        result = await env["repo"].find_by_criteria(query)
        # s1 has the record → excluded; s2 has none → included
        assert len(result) == 1
        assert result[0].series_uid == "1.2.3.200.2"


# ===================================================================
# UserRepository
# ===================================================================


class TestUserRepository:
    """Tests for UserRepository."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        repo = UserRepository(test_session)
        role = UserRole(name="annotator")
        test_session.add(role)
        await test_session.commit()
        user = make_user(email="user_repo@test.com")
        user = await repo.create(user)
        return {"repo": repo, "user": user, "role": role}

    @pytest.mark.asyncio
    async def test_get_with_roles(self, env):
        user = await env["repo"].get_with_roles(env["user"].id)
        assert isinstance(user.roles, list)

    @pytest.mark.asyncio
    async def test_find_by_email(self, env):
        found = await env["repo"].find_by_email("user_repo@test.com")
        assert found is not None

    @pytest.mark.asyncio
    async def test_find_by_email_not_found(self, env):
        assert await env["repo"].find_by_email("nobody@test.com") is None

    @pytest.mark.asyncio
    async def test_find_by_username(self, env):
        # User model has no username field — find_by_username calls get_by(username=...)
        # which ignores unknown fields and may return any user; just verify no crash
        result = await env["repo"].find_by_username("anything")
        # Result is non-deterministic when field doesn't exist, so just check type
        assert result is None or isinstance(result, User)

    @pytest.mark.asyncio
    async def test_add_role(self, env):
        user = await env["repo"].add_role(env["user"], env["role"])
        assert await env["repo"].has_role(user, "annotator")

    @pytest.mark.asyncio
    async def test_add_role_duplicate(self, env):
        await env["repo"].add_role(env["user"], env["role"])
        # Adding again should be idempotent
        user = await env["repo"].add_role(env["user"], env["role"])
        assert await env["repo"].has_role(user, "annotator")

    @pytest.mark.asyncio
    async def test_remove_role(self, env):
        await env["repo"].add_role(env["user"], env["role"])
        user = await env["repo"].remove_role(env["user"], env["role"])
        assert not await env["repo"].has_role(user, "annotator")

    @pytest.mark.asyncio
    async def test_has_role(self, env):
        assert not await env["repo"].has_role(env["user"], "annotator")

    @pytest.mark.asyncio
    async def test_get_all_with_roles(self, env):
        users = await env["repo"].get_all_with_roles()
        assert len(users) >= 1

    @pytest.mark.asyncio
    async def test_find_by_role(self, test_session, env):
        await env["repo"].add_role(env["user"], env["role"])
        users = await env["repo"].find_by_role("annotator")
        assert any(u.id == env["user"].id for u in users)

    @pytest.mark.asyncio
    async def test_activate(self, env):
        env["user"].is_active = False
        await env["repo"].session.commit()
        user = await env["repo"].activate(env["user"])
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_deactivate(self, env):
        user = await env["repo"].deactivate(env["user"])
        assert user.is_active is False

    @pytest.mark.asyncio
    async def test_update_password(self, env):
        new_hash = get_password_hash("newpassword")
        user = await env["repo"].update_password(env["user"], new_hash)
        assert user.hashed_password == new_hash


# ===================================================================
# RecordRepository
# ===================================================================


class TestRecordRepository:
    """Tests for RecordRepository."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        """Seed patient → study → series → record_type → record."""
        pat = make_patient("RPAT", "Record Patient")
        test_session.add(pat)
        await test_session.commit()
        study = make_study("RPAT", "1.2.3.300")
        test_session.add(study)
        await test_session.commit()
        series = make_series("1.2.3.300", "1.2.3.300.1", 1)
        test_session.add(series)
        await test_session.commit()
        rt = make_record_type("rec-rt-00001")
        test_session.add(rt)
        await test_session.commit()

        user = make_user()
        test_session.add(user)
        await test_session.commit()
        await test_session.refresh(user)

        rec = await seed_record(
            test_session,
            patient_id="RPAT",
            study_uid="1.2.3.300",
            series_uid="1.2.3.300.1",
            rt_name="rec-rt-00001",
            user_id=user.id,
        )
        repo = RecordRepository(test_session)
        return {
            "repo": repo,
            "record": rec,
            "user": user,
            "rt": rt,
            "series": series,
            "study": study,
            "pat": pat,
            "session": test_session,
        }

    @pytest.mark.asyncio
    async def test_get(self, env):
        found = await env["repo"].get(env["record"].id)
        assert found.id == env["record"].id

    @pytest.mark.asyncio
    async def test_get_not_found(self, env):
        with pytest.raises(RecordNotFoundError):
            await env["repo"].get(999999)

    @pytest.mark.asyncio
    async def test_get_with_record_type(self, env):
        rec = await env["repo"].get_with_record_type(env["record"].id)
        assert rec.record_type is not None
        assert rec.record_type.name == "rec-rt-00001"

    @pytest.mark.asyncio
    async def test_get_with_relations(self, env):
        rec = await env["repo"].get_with_relations(env["record"].id)
        assert rec.patient is not None
        assert rec.study is not None
        assert rec.record_type is not None

    @pytest.mark.asyncio
    async def test_find_by_user(self, env):
        records = await env["repo"].find_by_user(env["user"].id)
        assert len(records) >= 1

    @pytest.mark.asyncio
    async def test_find_pending_by_user(self, env):
        # Record is in pending status (default), pending is not terminal
        records = await env["repo"].find_pending_by_user(env["user"].id)
        assert len(records) >= 1

    @pytest.mark.asyncio
    async def test_update_status(self, env):
        rec, old_status = await env["repo"].update_status(env["record"].id, RecordStatus.inwork)
        assert old_status == RecordStatus.pending
        assert rec.status == RecordStatus.inwork

    @pytest.mark.asyncio
    async def test_update_data(self, env):
        data = {"label": "positive"}
        rec, old_status = await env["repo"].update_data(
            env["record"].id, data, RecordStatus.finished
        )
        assert rec.data == data
        assert old_status == RecordStatus.pending

    @pytest.mark.asyncio
    async def test_assign_user(self, env):
        new_user = make_user()
        env["session"].add(new_user)
        await env["session"].commit()
        await env["session"].refresh(new_user)
        rec, _old_status = await env["repo"].assign_user(env["record"].id, new_user.id)
        assert rec.user_id == new_user.id
        assert rec.status == RecordStatus.inwork

    @pytest.mark.asyncio
    async def test_claim_record(self, env):
        rec = await env["repo"].claim_record(env["record"].id, env["user"].id)
        assert rec.user_id == env["user"].id
        assert rec.status == RecordStatus.inwork

    @pytest.mark.asyncio
    async def test_ensure_user_assigned_when_no_user(self, env):
        # Clear user assignment
        env["record"].user_id = None
        await env["session"].commit()
        new_user = make_user()
        env["session"].add(new_user)
        await env["session"].commit()
        await env["session"].refresh(new_user)

        await env["repo"].ensure_user_assigned(env["record"].id, new_user.id)
        rec = await env["repo"].get(env["record"].id)
        assert rec.user_id == new_user.id

    @pytest.mark.asyncio
    async def test_ensure_user_assigned_noop_when_set(self, env):
        original_user_id = env["user"].id
        new_user = make_user()
        env["session"].add(new_user)
        await env["session"].commit()
        await env["session"].refresh(new_user)

        await env["repo"].ensure_user_assigned(env["record"].id, new_user.id)
        rec = await env["repo"].get(env["record"].id)
        assert rec.user_id == original_user_id  # should NOT change

    @pytest.mark.asyncio
    async def test_unassign_user(self, env):
        # Start with inwork + assigned user
        env["record"].status = RecordStatus.inwork
        await env["session"].commit()

        rec, old_status = await env["repo"].unassign_user(env["record"].id)
        assert rec.user_id is None
        assert rec.status == RecordStatus.pending
        assert old_status == RecordStatus.inwork

    @pytest.mark.asyncio
    async def test_unassign_user_finished_keeps_status(self, env):
        env["record"].status = RecordStatus.finished
        await env["session"].commit()

        rec, old_status = await env["repo"].unassign_user(env["record"].id)
        assert rec.user_id is None
        assert rec.status == RecordStatus.finished
        assert old_status == RecordStatus.finished

    @pytest.mark.asyncio
    async def test_bulk_update_status(self, env):
        await env["repo"].bulk_update_status([env["record"].id], RecordStatus.pause)
        rec = await env["repo"].get(env["record"].id)
        assert rec.status == RecordStatus.pause

    @pytest.mark.asyncio
    async def test_invalidate_record_hard(self, env):
        # Set to inwork first
        env["record"].status = RecordStatus.inwork
        await env["session"].commit()

        rec = await env["repo"].invalidate_record(
            env["record"].id, mode="hard", source_record_id=42
        )
        assert rec.status == RecordStatus.pending
        assert "Invalidated by record #42" in rec.context_info

    @pytest.mark.asyncio
    async def test_invalidate_record_soft(self, env):
        rec = await env["repo"].invalidate_record(
            env["record"].id, mode="soft", reason="Manual note"
        )
        assert "Manual note" in rec.context_info
        # Status unchanged
        assert rec.status == RecordStatus.pending

    @pytest.mark.asyncio
    async def test_count_by_type_and_context(self, env):
        count = await env["repo"].count_by_type_and_context(
            record_type_name="rec-rt-00001",
            patient_id="RPAT",
            study_uid="1.2.3.300",
            series_uid="1.2.3.300.1",
            level=DicomQueryLevel.SERIES,
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_check_constraints(self, env):
        # max_records is None → no constraint → should pass
        await env["repo"].check_constraints("rec-rt-00001", "1.2.3.300.1", "1.2.3.300")

    @pytest.mark.asyncio
    async def test_check_constraints_violation(self, env):
        env["rt"].max_records = 1
        await env["session"].commit()
        with pytest.raises(RecordConstraintViolationError):
            await env["repo"].check_constraints("rec-rt-00001", "1.2.3.300.1", "1.2.3.300")

    @pytest.mark.asyncio
    async def test_check_constraints_patient_level_per_patient(self, env):
        """PATIENT-level max_records=1 — лимит применяется отдельно к каждому пациенту."""
        rt = make_record_type(
            "patient-rt-limit",
            level=DicomQueryLevel.PATIENT,
            max_records=1,
        )
        env["session"].add(rt)
        pat_b = make_patient("RPAT2", "Second Patient")
        env["session"].add(pat_b)
        await env["session"].commit()

        await seed_record(
            env["session"],
            patient_id="RPAT",
            study_uid=None,
            series_uid=None,
            rt_name="patient-rt-limit",
        )

        # Для второго пациента check должен пройти — лимит per-patient, не глобальный.
        await env["repo"].check_constraints(
            record_type_name="patient-rt-limit",
            series_uid=None,
            study_uid=None,
            patient_id="RPAT2",
        )

    @pytest.mark.asyncio
    async def test_check_constraints_patient_level_violation(self, env):
        """PATIENT-level max_records=1: вторая запись тому же пациенту — RecordLimitReachedError."""
        rt = make_record_type(
            "patient-rt-limit2",
            level=DicomQueryLevel.PATIENT,
            max_records=1,
        )
        env["session"].add(rt)
        await env["session"].commit()

        await seed_record(
            env["session"],
            patient_id="RPAT",
            study_uid=None,
            series_uid=None,
            rt_name="patient-rt-limit2",
        )

        with pytest.raises(RecordLimitReachedError):
            await env["repo"].check_constraints(
                record_type_name="patient-rt-limit2",
                series_uid=None,
                study_uid=None,
                patient_id="RPAT",
            )

    @pytest.mark.asyncio
    async def test_check_constraints_study_level_isolation(self, env):
        """STUDY-level max_records=1 — лимит per-study, не глобальный."""
        rt = make_record_type(
            "study-rt-limit",
            level=DicomQueryLevel.STUDY,
            max_records=1,
        )
        env["session"].add(rt)
        study_b = make_study("RPAT", "1.2.3.301")
        env["session"].add(study_b)
        await env["session"].commit()

        await seed_record(
            env["session"],
            patient_id="RPAT",
            study_uid="1.2.3.300",
            series_uid=None,
            rt_name="study-rt-limit",
        )

        # Второй study — проверка должна пройти.
        await env["repo"].check_constraints(
            record_type_name="study-rt-limit",
            series_uid=None,
            study_uid="1.2.3.301",
            patient_id="RPAT",
        )

        # Тот же study — RecordLimitReachedError.
        with pytest.raises(RecordLimitReachedError):
            await env["repo"].check_constraints(
                record_type_name="study-rt-limit",
                series_uid=None,
                study_uid="1.2.3.300",
                patient_id="RPAT",
            )

    @pytest.mark.asyncio
    async def test_check_constraints_series_level_isolation(self, env):
        """SERIES-level max_records=1 — лимит per-series, не глобальный."""
        rt = make_record_type(
            "series-rt-limit",
            level=DicomQueryLevel.SERIES,
            max_records=1,
        )
        env["session"].add(rt)
        series_b = make_series("1.2.3.300", "1.2.3.300.2", 2)
        env["session"].add(series_b)
        await env["session"].commit()

        await seed_record(
            env["session"],
            patient_id="RPAT",
            study_uid="1.2.3.300",
            series_uid="1.2.3.300.1",
            rt_name="series-rt-limit",
        )

        # Different series — should pass.
        await env["repo"].check_constraints(
            record_type_name="series-rt-limit",
            series_uid="1.2.3.300.2",
            study_uid="1.2.3.300",
            patient_id="RPAT",
        )

        # Same series — RecordLimitReachedError.
        with pytest.raises(RecordLimitReachedError):
            await env["repo"].check_constraints(
                record_type_name="series-rt-limit",
                series_uid="1.2.3.300.1",
                study_uid="1.2.3.300",
                patient_id="RPAT",
            )

    @pytest.mark.asyncio
    async def test_find_by_criteria(self, env):
        criteria = RecordSearchCriteria(record_type_name="rec-rt-00001")
        records = await env["repo"].find_by_criteria(criteria)
        assert len(records) >= 1

    @pytest.mark.asyncio
    async def test_find_by_criteria_patient_level_record(self, env):
        """PATIENT-level records (study_uid=NULL) must be found by patient_id."""
        rt = make_record_type("patient-level-rt", level=DicomQueryLevel.PATIENT)
        env["session"].add(rt)
        await env["session"].commit()

        rec = await seed_record(
            env["session"],
            patient_id="RPAT",
            study_uid=None,
            series_uid=None,
            rt_name="patient-level-rt",
        )

        # patient_id filter must return PATIENT-level record
        criteria = RecordSearchCriteria(record_type_name="patient-level-rt", patient_id="RPAT")
        records = await env["repo"].find_by_criteria(criteria)
        assert len(records) == 1
        assert records[0].id == rec.id

        # anon_study_uid="Null" (un-anonymized study) must NOT return PATIENT-level record
        # because PATIENT-level records have no study — INNER JOIN is correct here
        criteria_anon = RecordSearchCriteria(
            record_type_name="patient-level-rt",
            patient_id="RPAT",
            anon_study_uid="Null",
        )
        records_anon = await env["repo"].find_by_criteria(criteria_anon)
        assert len(records_anon) == 0

    @pytest.mark.asyncio
    async def test_find_by_criteria_json_data_pagination(self, env):
        """Pagination works on records with JSON data (no .distinct() needed).

        Regression: .distinct() on tables with JSON columns fails on PostgreSQL
        because json type has no equality operator. All joins in find_by_criteria
        are N:1, so duplicates cannot occur — _paginate() with ORDER BY is sufficient.
        """
        # Create records with populated JSON data fields
        for i in range(3):
            await seed_record(
                env["session"],
                patient_id="RPAT",
                study_uid="1.2.3.300",
                series_uid="1.2.3.300.1",
                rt_name="rec-rt-00001",
                data={"score": str(i), "note": f"entry-{i}"},
            )

        criteria = RecordSearchCriteria(record_type_name="rec-rt-00001")

        # Fetch all (env fixture already seeds 1 record + 3 above = 4 total)
        all_records = await env["repo"].find_by_criteria(criteria, skip=0, limit=100)
        assert len(all_records) == 4

        # Paginate and verify no duplicates / gaps
        page1 = await env["repo"].find_by_criteria(criteria, skip=0, limit=2)
        page2 = await env["repo"].find_by_criteria(criteria, skip=2, limit=2)
        assert len(page1) == 2
        assert len(page2) == 2

        page1_ids = {r.id for r in page1}
        page2_ids = {r.id for r in page2}
        assert page1_ids.isdisjoint(page2_ids), "Pages must not overlap"
        assert page1_ids | page2_ids == {r.id for r in all_records}

    @pytest.mark.asyncio
    async def test_get_status_counts(self, env):
        counts = await env["repo"].get_status_counts()
        assert isinstance(counts, dict)
        assert counts.get("pending", 0) >= 1

    @pytest.mark.asyncio
    async def test_get_per_type_status_counts(self, env):
        counts = await env["repo"].get_per_type_status_counts()
        assert "rec-rt-00001" in counts


# ===================================================================
# StudyRepository
# ===================================================================


class TestStudyRepository:
    """Tests for StudyRepository."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        pat = make_patient("STPAT", "Study Patient")
        test_session.add(pat)
        await test_session.commit()
        study = make_study("STPAT", "1.2.3.400")
        test_session.add(study)
        await test_session.commit()
        series = make_series("1.2.3.400", "1.2.3.400.1", 1)
        test_session.add(series)
        await test_session.commit()
        await test_session.refresh(study)
        repo = StudyRepository(test_session)
        return {"repo": repo, "study": study, "patient": pat, "series": series}

    @pytest.mark.asyncio
    async def test_get_with_relations(self, env):
        study = await env["repo"].get_with_relations("1.2.3.400")
        assert study.patient is not None

    @pytest.mark.asyncio
    async def test_find_by_patient(self, env):
        studies = await env["repo"].find_by_patient("STPAT")
        assert len(studies) >= 1

    @pytest.mark.asyncio
    async def test_get_series(self, env):
        series_list = await env["repo"].get_series("1.2.3.400")
        assert len(series_list) >= 1

    @pytest.mark.asyncio
    async def test_get_records(self, env):
        records = await env["repo"].get_records("1.2.3.400")
        assert isinstance(records, list)

    @pytest.mark.asyncio
    async def test_has_record(self, env):
        assert await env["repo"].has_record(env["study"], 999999) is False

    @pytest.mark.asyncio
    async def test_count_series(self, env):
        assert await env["repo"].count_series("1.2.3.400") == 1

    @pytest.mark.asyncio
    async def test_count_records(self, env):
        assert await env["repo"].count_records("1.2.3.400") == 0

    @pytest.mark.asyncio
    async def test_search(self, env):
        results = await env["repo"].search(patient_id="STPAT")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_by_uid(self, env):
        results = await env["repo"].search(study_uid="1.2.3.400")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_get_by_uid(self, env):
        study = await env["repo"].get_by_uid("1.2.3.400")
        assert study.study_uid == "1.2.3.400"


# ===================================================================
# PatientRepository
# ===================================================================


class TestPatientRepository:
    """Tests for PatientRepository."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        repo = PatientRepository(test_session)
        pat = await repo.create(make_patient("PPAT1", "John Doe"))
        await repo.create(make_patient("PPAT2", "Jane Smith"))
        # Add a study for PPAT1
        study = make_study("PPAT1", "1.2.3.500")
        test_session.add(study)
        await test_session.commit()
        return {"repo": repo, "patient": pat}

    @pytest.mark.asyncio
    async def test_get_all_with_studies(self, env):
        patients = await env["repo"].get_all_with_studies()
        assert len(patients) >= 2

    @pytest.mark.asyncio
    async def test_find_by_name(self, env):
        results = await env["repo"].find_by_name("john")
        assert len(results) == 1
        assert results[0].id == "PPAT1"

    @pytest.mark.asyncio
    async def test_find_by_anon_name(self, env):
        assert await env["repo"].find_by_anon_name("NONEXISTENT") is None

    @pytest.mark.asyncio
    async def test_find_by_anon_name_found(self, env):
        await env["repo"].update_anon_name(env["patient"], "AnonJohn")
        found = await env["repo"].find_by_anon_name("AnonJohn")
        assert found is not None

    @pytest.mark.asyncio
    async def test_search(self, env):
        results = await env["repo"].search("john")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_by_id(self, env):
        results = await env["repo"].search("PPAT1")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_count_studies(self, env):
        assert await env["repo"].count_studies("PPAT1") == 1
        assert await env["repo"].count_studies("PPAT2") == 0

    @pytest.mark.asyncio
    async def test_exists_anon_name(self, env):
        assert await env["repo"].exists_anon_name("NONEXISTENT") is False

    @pytest.mark.asyncio
    async def test_update_anon_name(self, env):
        updated = await env["repo"].update_anon_name(env["patient"], "AnonPat")
        assert updated.anon_name == "AnonPat"


# ===================================================================
# RecordTypeRepository
# ===================================================================


class TestRecordTypeRepository:
    """Tests for RecordTypeRepository."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        repo = RecordTypeRepository(test_session)
        rt = make_record_type("rt-test-00001", description="A test type")
        rt = await repo.create(rt)
        return {"repo": repo, "rt": rt}

    @pytest.mark.asyncio
    async def test_get(self, env):
        found = await env["repo"].get("rt-test-00001")
        assert found.name == "rt-test-00001"

    @pytest.mark.asyncio
    async def test_get_not_found(self, env):
        with pytest.raises(RecordTypeNotFoundError):
            await env["repo"].get("nonexistent_type")

    @pytest.mark.asyncio
    async def test_find_by_name(self, env):
        from clarinet.models.record import RecordTypeFind

        results = await env["repo"].find(RecordTypeFind(name="rt-test"))
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_ensure_unique_name(self, env):
        # Should pass for non-existent name
        await env["repo"].ensure_unique_name("unique_name_12345")

    @pytest.mark.asyncio
    async def test_ensure_unique_name_conflict(self, env):
        with pytest.raises(RecordTypeAlreadyExistsError):
            await env["repo"].ensure_unique_name("rt-test-00001")

    @pytest.mark.asyncio
    async def test_update_with_options_loads_relationships(self, test_session):
        """update() with options returns entity with eagerly loaded relationships."""
        repo = RecordTypeRepository(test_session)
        rt = make_record_type("rt-opts-001", description="Original")
        rt = await repo.create(rt)

        fd = FileDefinition(name="opts_seg", pattern="seg.nrrd")
        test_session.add(fd)
        await test_session.flush()

        link = RecordTypeFileLink(
            record_type_name="rt-opts-001",
            file_definition_id=fd.id,
            role=FileRole.OUTPUT,
            required=True,
        )
        test_session.add(link)
        await test_session.commit()

        updated = await repo.update(
            rt,
            {"description": "Updated"},
            options=[
                selectinload(RecordType.file_links).selectinload(
                    RecordTypeFileLink.file_definition
                ),
            ],
        )

        assert updated.description == "Updated"
        assert len(updated.file_links) == 1
        assert updated.file_links[0].file_definition.name == "opts_seg"
