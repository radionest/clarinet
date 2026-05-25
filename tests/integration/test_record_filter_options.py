"""Integration tests for POST /records/filter-options.

Verifies the distinct-value endpoint returns the user's full RBAC scope
(NOT filtered by the user-driven UI filters in the request body) and
includes the ``__unassigned__`` sentinel when scope contains any record
with ``user_id IS NULL``.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from clarinet.api.app import app
from clarinet.api.auth_config import current_active_user, current_superuser
from clarinet.api.dependencies import current_admin_user
from clarinet.models.record import Record, RecordType
from clarinet.models.study import Series, Study
from clarinet.models.user import User, UserRole, UserRolesLink
from clarinet.utils.auth import get_password_hash
from clarinet.utils.database import get_async_session
from tests.utils.urls import RECORDS_FILTER_OPTIONS

_UNASSIGNED = "__unassigned__"


# --- Fixtures (self-contained; mirror the RBAC test setup) ---


@pytest_asyncio.fixture
async def role_a(test_session):
    role = UserRole(name="role_a_filter")
    test_session.add(role)
    await test_session.commit()
    await test_session.refresh(role)
    return role


@pytest_asyncio.fixture
async def role_b(test_session):
    role = UserRole(name="role_b_filter")
    test_session.add(role)
    await test_session.commit()
    await test_session.refresh(role)
    return role


@pytest_asyncio.fixture
async def record_type_role_a(test_session, role_a):
    rt = RecordType(
        name="rt-role-a-filter",
        role_name=role_a.name,
        level="SERIES",
        label="Role A filter type",
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def record_type_role_b(test_session, role_b):
    rt = RecordType(
        name="rt-role-b-filter",
        role_name=role_b.name,
        level="SERIES",
        label="Role B filter type",
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def superuser(test_session):
    user = User(
        id=uuid4(),
        email="su_filter@test.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def regular_user_a(test_session, role_a):
    from sqlalchemy.orm import selectinload
    from sqlmodel import select

    uid = uuid4()
    user = User(
        id=uid,
        email="user_a_filter@test.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()
    test_session.add(UserRolesLink(user_id=uid, role_name=role_a.name))
    await test_session.commit()

    stmt = select(User).where(User.id == uid).options(selectinload(User.roles))
    result = await test_session.execute(stmt)
    return result.scalars().first()


@pytest_asyncio.fixture
async def superuser_client(test_session, test_settings, superuser):
    async def override_get_session():
        yield test_session

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: superuser
    app.dependency_overrides[current_superuser] = lambda: superuser
    app.dependency_overrides[current_admin_user] = lambda: superuser

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def regular_a_client(test_session, test_settings, regular_user_a):
    async def override_get_session():
        yield test_session

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: regular_user_a

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def diverse_scope(
    test_session,
    record_type_role_a,
    record_type_role_b,
):
    """Seed 3 patients, 2 record types, and a mix of assigned/unassigned records.

    Returns dict with the seeded patients, studies, series, users for assertions.
    """
    from tests.utils.factories import make_patient

    patients = []
    studies = []
    series_list = []
    for i, pid in enumerate(["PAT_FILT_A", "PAT_FILT_B", "PAT_FILT_C"]):
        p = make_patient(pid, f"Patient {i}", anon_name=f"ANON_{i}")
        test_session.add(p)
        await test_session.commit()
        await test_session.refresh(p)
        patients.append(p)

        study = Study(
            patient_id=p.id,
            study_uid=f"2.25.{1000 + i}",
            date=datetime.now(UTC).date(),
            anon_uid=f"ANON_STUDY_{i}",
        )
        test_session.add(study)
        await test_session.commit()
        await test_session.refresh(study)
        studies.append(study)

        series = Series(
            study_uid=study.study_uid,
            series_uid=f"2.25.{2000 + i}.1",
            series_number=1,
            series_description=f"Series {i}",
        )
        test_session.add(series)
        await test_session.commit()
        await test_session.refresh(series)
        series_list.append(series)

    users = []
    for i in range(2):
        u = User(
            id=uuid4(),
            email=f"assignee_{i}@filter.test",
            hashed_password=get_password_hash("x"),
            is_active=True,
            is_verified=True,
            is_superuser=False,
        )
        test_session.add(u)
        await test_session.commit()
        await test_session.refresh(u)
        users.append(u)

    records = [
        # patient 0, type_a, assigned user[0]
        Record(
            patient_id=patients[0].id,
            study_uid=studies[0].study_uid,
            series_uid=series_list[0].series_uid,
            record_type_name=record_type_role_a.name,
            record_type=record_type_role_a,
            user_id=users[0].id,
            status="pending",
        ),
        # patient 1, type_a, unassigned
        Record(
            patient_id=patients[1].id,
            study_uid=studies[1].study_uid,
            series_uid=series_list[1].series_uid,
            record_type_name=record_type_role_a.name,
            record_type=record_type_role_a,
            user_id=None,
            status="pending",
        ),
        # patient 2, type_b, assigned user[1]
        Record(
            patient_id=patients[2].id,
            study_uid=studies[2].study_uid,
            series_uid=series_list[2].series_uid,
            record_type_name=record_type_role_b.name,
            record_type=record_type_role_b,
            user_id=users[1].id,
            status="pending",
        ),
    ]
    for r in records:
        test_session.add(r)
    await test_session.commit()
    for r in records:
        await test_session.refresh(r)

    return {
        "patients": patients,
        "users": users,
        "records": records,
        "record_type_a": record_type_role_a,
        "record_type_b": record_type_role_b,
    }


# --- Tests ---


@pytest.mark.asyncio
async def test_superuser_sees_all_distinct_values(superuser_client: AsyncClient, diverse_scope):
    """Superuser scope: all 3 patients, both record types, both users + __unassigned__."""
    resp = await superuser_client.post(RECORDS_FILTER_OPTIONS, json={})
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["patients"]) == {"PAT_FILT_A", "PAT_FILT_B", "PAT_FILT_C"}
    assert set(data["record_types"]) == {
        diverse_scope["record_type_a"].name,
        diverse_scope["record_type_b"].name,
    }
    assigned_user_ids = {str(u.id) for u in diverse_scope["users"]}
    assert assigned_user_ids.issubset(set(data["users"]))
    assert data["users"][0] == _UNASSIGNED


@pytest.mark.asyncio
async def test_regular_user_scope_limited_by_role(regular_a_client: AsyncClient, diverse_scope):
    """Regular user with role_a sees only the role_a record type and its records.

    Patient_ids are masked (see ``test_anonymized_patient_ids_masked_for_non_superuser``);
    we assert on the count + anon_id equivalence rather than raw patient_id.
    """
    resp = await regular_a_client.post(RECORDS_FILTER_OPTIONS, json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["record_types"] == [diverse_scope["record_type_a"].name]
    # PAT_FILT_A (assigned) + PAT_FILT_B (unassigned) are in scope.
    # PAT_FILT_C has only a role_b record → out of scope.
    expected_visible = {
        diverse_scope["patients"][0].anon_id,
        diverse_scope["patients"][1].anon_id,
    }
    assert set(data["patients"]) == expected_visible
    assert diverse_scope["patients"][2].anon_id not in data["patients"]


@pytest.mark.asyncio
async def test_anonymized_patient_ids_masked_for_non_superuser(
    regular_a_client: AsyncClient, diverse_scope
):
    """Real patient_ids must never appear in the response for a non-superuser
    when the patient has been anonymized (anon_name set).

    Mirrors the guarantee provided by ``mask_record_patient_data`` for
    every Record-returning endpoint.
    """
    resp = await regular_a_client.post(RECORDS_FILTER_OPTIONS, json={})
    data = resp.json()
    # None of the real PatientIDs from `diverse_scope` may appear.
    for raw_pid in ("PAT_FILT_A", "PAT_FILT_B", "PAT_FILT_C"):
        assert raw_pid not in data["patients"]
    # Every value must be a valid anon_id of an in-scope patient.
    in_scope_anon_ids = {p.anon_id for p in diverse_scope["patients"][:2]}
    assert set(data["patients"]).issubset(in_scope_anon_ids)


@pytest.mark.asyncio
async def test_superuser_sees_real_patient_ids(superuser_client: AsyncClient, diverse_scope):
    """Superusers always get real patient_ids — masking applies to non-superusers only."""
    resp = await superuser_client.post(RECORDS_FILTER_OPTIONS, json={})
    data = resp.json()
    assert set(data["patients"]) == {"PAT_FILT_A", "PAT_FILT_B", "PAT_FILT_C"}


@pytest.mark.asyncio
async def test_filter_by_anon_patient_id_routes_through_anon_branch(
    regular_a_client: AsyncClient, diverse_scope
):
    """A non-superuser selecting an anon_id from the dropdown submits it
    back through ``/records/find`` as a ``patient_id``; the repository
    auto-routes anon_id-shaped values through the patient_anon_id branch
    so the records are actually found.
    """
    target_anon = diverse_scope["patients"][0].anon_id  # PAT_FILT_A → anon_id
    resp = await regular_a_client.post("/api/records/find", json={"patient_id": target_anon})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    for item in items:
        # Non-superuser sees the masked id; per-RecordType opt-out keeps
        # the records but the mask layer rewrites patient_id.
        assert item["patient_id"] == target_anon


@pytest.mark.asyncio
async def test_unassigned_sentinel_present_when_scope_has_unassigned(
    superuser_client: AsyncClient, diverse_scope
):
    resp = await superuser_client.post(RECORDS_FILTER_OPTIONS, json={})
    users = resp.json()["users"]
    assert users[0] == _UNASSIGNED


@pytest.mark.asyncio
async def test_unassigned_sentinel_absent_when_all_assigned(
    test_session, superuser_client: AsyncClient, record_type_role_a
):
    from tests.utils.factories import make_patient

    p = make_patient("PAT_ALL_ASSIGNED", "All Assigned", anon_name="ANON_AA")
    test_session.add(p)
    await test_session.commit()
    study = Study(
        patient_id=p.id,
        study_uid="2.25.7000",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_S_AA",
    )
    test_session.add(study)
    await test_session.commit()
    series = Series(study_uid=study.study_uid, series_uid="2.25.7000.1", series_number=1)
    test_session.add(series)
    await test_session.commit()
    assignee = User(
        id=uuid4(),
        email="solo_assignee@filter.test",
        hashed_password=get_password_hash("x"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(assignee)
    await test_session.commit()
    rec = Record(
        patient_id=p.id,
        study_uid=study.study_uid,
        series_uid=series.series_uid,
        record_type_name=record_type_role_a.name,
        record_type=record_type_role_a,
        user_id=assignee.id,
        status="pending",
    )
    test_session.add(rec)
    await test_session.commit()

    resp = await superuser_client.post(RECORDS_FILTER_OPTIONS, json={})
    users = resp.json()["users"]
    assert _UNASSIGNED not in users


@pytest.mark.asyncio
async def test_body_filters_are_ignored(superuser_client: AsyncClient, diverse_scope):
    """Passing patient_id=PAT_FILT_A in body must NOT shrink the dropdown."""
    resp = await superuser_client.post(RECORDS_FILTER_OPTIONS, json={"patient_id": "PAT_FILT_A"})
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["patients"]) == {"PAT_FILT_A", "PAT_FILT_B", "PAT_FILT_C"}
    assert set(data["record_types"]) == {
        diverse_scope["record_type_a"].name,
        diverse_scope["record_type_b"].name,
    }


@pytest.mark.asyncio
async def test_empty_scope_returns_empty_lists(superuser_client: AsyncClient):
    """No records seeded → all lists empty + no sentinel."""
    resp = await superuser_client.post(RECORDS_FILTER_OPTIONS, json={})
    assert resp.status_code == 200
    assert resp.json() == {"patients": [], "record_types": [], "users": []}


@pytest.mark.asyncio
async def test_lists_are_sorted(superuser_client: AsyncClient, diverse_scope):
    resp = await superuser_client.post(RECORDS_FILTER_OPTIONS, json={})
    data = resp.json()
    assert data["patients"] == sorted(data["patients"])
    assert data["record_types"] == sorted(data["record_types"])
    # users list: __unassigned__ first (inserted), then sorted UUIDs
    assert data["users"][0] == _UNASSIGNED
    assert data["users"][1:] == sorted(data["users"][1:])
