"""Integration tests for Role-Based Access Control (RBAC).

This module tests role-based filtering and authorization for records:
- Superusers see all records
- Non-superusers only see records matching their assigned roles
- Records with role_name=NULL are superuser-only
- Patient data masking for anonymized patients
- Admin endpoints require superuser access
"""

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from clarinet.api.app import app
from clarinet.api.auth_config import current_active_user, current_superuser
from clarinet.api.dependencies import current_admin_user
from clarinet.models.record import Record, RecordType
from clarinet.models.user import User, UserRole, UserRolesLink
from clarinet.utils.auth import get_password_hash
from clarinet.utils.database import get_async_session

# Fixtures


@pytest_asyncio.fixture
async def role_a(test_session):
    """Create role_a_test UserRole."""
    role = UserRole(name="role_a_test")
    test_session.add(role)
    await test_session.commit()
    await test_session.refresh(role)
    return role


@pytest_asyncio.fixture
async def role_b(test_session):
    """Create role_b_test UserRole."""
    role = UserRole(name="role_b_test")
    test_session.add(role)
    await test_session.commit()
    await test_session.refresh(role)
    return role


@pytest_asyncio.fixture
async def record_type_role_a(test_session, role_a):
    """Create RecordType with role_name=role_a_test."""
    record_type = RecordType(
        name="rtype-role-a-test",
        role_name="role_a_test",
        level="SERIES",
        label="Role A Test Type",
        description="Test record type for role A",
    )
    test_session.add(record_type)
    await test_session.commit()
    await test_session.refresh(record_type)
    return record_type


@pytest_asyncio.fixture
async def record_type_role_b(test_session, role_b):
    """Create RecordType with role_name=role_b_test."""
    record_type = RecordType(
        name="rtype-role-b-test",
        role_name="role_b_test",
        level="SERIES",
        label="Role B Test Type",
        description="Test record type for role B",
    )
    test_session.add(record_type)
    await test_session.commit()
    await test_session.refresh(record_type)
    return record_type


@pytest_asyncio.fixture
async def record_type_null_role(test_session):
    """Create RecordType with role_name=None (superuser-only)."""
    record_type = RecordType(
        name="rtype-null-test",
        role_name=None,
        level="SERIES",
        label="Null Role Test Type",
        description="Test record type with no role constraint",
    )
    test_session.add(record_type)
    await test_session.commit()
    await test_session.refresh(record_type)
    return record_type


@pytest_asyncio.fixture
async def user_with_role_a(test_session, role_a):
    """Create non-superuser with role_a_test assigned."""
    from sqlalchemy.orm import selectinload
    from sqlmodel import select

    user_id = uuid4()
    user = User(
        id=user_id,
        email="user_role_a@test.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()

    # Link user to role
    link = UserRolesLink(user_id=user_id, role_name="role_a_test")
    test_session.add(link)
    await test_session.commit()

    # Reload with roles relation populated
    stmt = select(User).where(User.id == user_id).options(selectinload(User.roles))
    result = await test_session.execute(stmt)
    user = result.scalars().first()

    return user


@pytest_asyncio.fixture
async def superuser(test_session):
    """Create superuser."""
    user = User(
        id=uuid4(),
        email="superuser@test.com",
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
async def record_role_a(test_session, test_patient, test_study, test_series, record_type_role_a):
    """Create Record with record_type_name=rtype_role_a_test."""
    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        record_type_name=record_type_role_a.name,
        record_type=record_type_role_a,
        status="pending",
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)
    return record


@pytest_asyncio.fixture
async def record_role_b(test_session, test_patient, test_study, test_series, record_type_role_b):
    """Create Record with record_type_name=rtype_role_b_test."""
    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        record_type_name=record_type_role_b.name,
        record_type=record_type_role_b,
        status="pending",
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)
    return record


@pytest_asyncio.fixture
async def record_null_role(
    test_session, test_patient, test_study, test_series, record_type_null_role
):
    """Create Record with record_type_name=rtype_null_test__ (superuser-only)."""
    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        record_type_name=record_type_null_role.name,
        record_type=record_type_null_role,
        status="pending",
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)
    return record


@pytest_asyncio.fixture
async def role_a_client(test_session, test_settings, user_with_role_a):
    """AsyncClient with dependency override for user_with_role_a."""

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: user_with_role_a

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import clarinet.api.auth_config

        clarinet.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def superuser_client(test_session, test_settings, superuser):
    """AsyncClient with dependency override for superuser."""

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: superuser
    app.dependency_overrides[current_superuser] = lambda: superuser
    app.dependency_overrides[current_admin_user] = lambda: superuser

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import clarinet.api.auth_config

        clarinet.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_role(test_session):
    """Create the built-in 'admin' UserRole used by AdminUserDep."""
    role = UserRole(name="admin")
    test_session.add(role)
    await test_session.commit()
    await test_session.refresh(role)
    return role


@pytest_asyncio.fixture
async def admin_role_user(test_session, admin_role):
    """Create a non-superuser user assigned to the 'admin' role."""
    from sqlalchemy.orm import selectinload
    from sqlmodel import select

    user_id = uuid4()
    user = User(
        id=user_id,
        email="admin_role@test.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()

    test_session.add(UserRolesLink(user_id=user_id, role_name="admin"))
    await test_session.commit()

    stmt = select(User).where(User.id == user_id).options(selectinload(User.roles))
    result = await test_session.execute(stmt)
    user = result.scalars().first()
    return user


@pytest_asyncio.fixture
async def admin_role_client(test_session, test_settings, admin_role_user):
    """AsyncClient for a non-superuser holding the 'admin' role.

    Overrides ``current_active_user`` only — admin endpoints must reach
    ``current_admin_user`` for real, which checks the role on ``admin_role_user``.
    """

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: admin_role_user

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import clarinet.api.auth_config

        clarinet.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        yield ac

    app.dependency_overrides.clear()


# Tests


@pytest.mark.asyncio
async def test_find_records_superuser_sees_all(
    superuser_client, record_role_a, record_role_b, record_null_role
):
    """Superuser POST /api/records/find should see all 3 records."""
    response = await superuser_client.post("/api/records/find", json={})
    assert response.status_code == 200
    data = response.json()["items"]
    assert len(data) == 3
    record_ids = {r["id"] for r in data}
    assert record_ids == {record_role_a.id, record_role_b.id, record_null_role.id}


@pytest.mark.asyncio
async def test_find_records_role_user_sees_own_role(
    role_a_client, record_role_a, record_role_b, record_null_role
):
    """Non-superuser with role_a_test POST /api/records/find should only see record_role_a."""
    response = await role_a_client.post("/api/records/find", json={})
    assert response.status_code == 200
    data = response.json()["items"]
    assert len(data) == 1
    assert data[0]["id"] == record_role_a.id
    assert data[0]["record_type"]["name"] == "rtype-role-a-test"


@pytest.mark.asyncio
async def test_get_record_by_id_own_role_ok(role_a_client, record_role_a):
    """Non-superuser can GET /api/records/{id} for a record matching their role."""
    response = await role_a_client.get(f"/api/records/{record_role_a.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == record_role_a.id
    assert data["record_type"]["name"] == "rtype-role-a-test"


@pytest.mark.asyncio
async def test_get_record_by_id_other_role_forbidden(role_a_client, record_role_b):
    """Non-superuser cannot GET /api/records/{id} for a record with a different role."""
    response = await role_a_client.get(f"/api/records/{record_role_b.id}")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_record_by_id_null_role_forbidden(role_a_client, record_null_role):
    """Non-superuser cannot GET /api/records/{id} for a record with role_name=NULL."""
    response = await role_a_client.get(f"/api/records/{record_null_role.id}")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_find_records_role_filtering(
    role_a_client, record_role_a, record_role_b, record_null_role
):
    """POST /api/records/find should only return records matching user's role."""
    response = await role_a_client.post(
        "/api/records/find",
        json={"patient_id": "TEST_PAT001"},
    )
    assert response.status_code == 200
    data = response.json()["items"]
    assert len(data) == 1
    assert data[0]["id"] == record_role_a.id


@pytest.mark.asyncio
async def test_patients_endpoint_superuser_ok(superuser_client, test_patient):
    """Superuser can access GET /api/patients."""
    response = await superuser_client.get("/api/patients")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(p["id"] == test_patient.id for p in data)


@pytest.mark.asyncio
async def test_patients_endpoint_non_admin_forbidden(role_a_client, test_patient):
    """Non-superuser cannot access GET /api/patients (admin-only endpoint)."""
    response = await role_a_client.get("/api/patients")
    # Depending on how fastapi-users handles current_superuser dependency,
    # this could be 401 (Unauthorized) or 403 (Forbidden)
    assert response.status_code in [401, 403]


@pytest.mark.asyncio
async def test_patient_masking_for_non_admin(
    test_session, role_a_client, record_role_a, test_patient
):
    """Non-superuser should see anonymized patient_id when patient has auto_id set.

    When patient.anon_name is not None AND patient.auto_id is set,
    non-superusers should see the anon_id (CLARINET_XX format) instead of real patient_id.
    """
    # Update patient to have auto_id so anon_id is computed
    test_patient.auto_id = 123
    test_session.add(test_patient)
    await test_session.commit()

    response = await role_a_client.get(f"/api/records/{record_role_a.id}")
    assert response.status_code == 200
    data = response.json()

    # Patient should be masked
    assert data["patient"]["id"] == "CLARINET_123"  # anon_id
    assert data["patient"]["name"] == "ANON_001"  # anon_name
    assert data["patient_id"] == "CLARINET_123"  # top-level masked


@pytest.mark.asyncio
async def test_superuser_sees_real_patient_data(
    test_session, superuser_client, record_role_a, test_patient
):
    """Superuser should always see real patient data, even when anonymized."""
    # Update patient to have auto_id
    test_patient.auto_id = 123
    test_session.add(test_patient)
    await test_session.commit()

    response = await superuser_client.get(f"/api/records/{record_role_a.id}")
    assert response.status_code == 200
    data = response.json()

    # Superuser sees real data
    assert data["patient"]["id"] == "TEST_PAT001"
    assert data["patient"]["name"] == "Test Patient"
    assert data["patient_id"] == "TEST_PAT001"


@pytest.mark.asyncio
async def test_get_available_types_filtered_by_role(
    role_a_client, record_role_a, record_type_role_a, record_type_role_b, record_type_null_role
):
    """GET /api/records/available_types returns counts only for types with pending records.

    The endpoint returns a dict[str, int] mapping type names to counts of pending records
    that match the user's roles.
    """
    response = await role_a_client.get("/api/records/available_types")
    assert response.status_code == 200
    data = response.json()

    # The endpoint only returns types that have BOTH:
    # 1. Pending records
    # 2. Match user's roles
    # Since we have pending records only for role_a, we should see it
    assert "rtype-role-a-test" in data
    assert data["rtype-role-a-test"] >= 1
    # Should not see types outside user's role
    assert "rtype-role-b-test" not in data
    assert "rtype-null-test" not in data


@pytest.mark.asyncio
async def test_superuser_sees_all_available_types(
    superuser_client,
    record_role_a,
    record_role_b,
    record_type_role_a,
    record_type_role_b,
    record_type_null_role,
):
    """Superuser GET /api/records/available_types sees types with pending records.

    The endpoint returns dict[str, int] mapping type names to counts.
    Superusers see all types (not filtered by role).
    """
    response = await superuser_client.get("/api/records/available_types")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_my_records_endpoint_filtered_by_role(
    test_session, role_a_client, user_with_role_a, record_role_a, record_role_b
):
    """POST /api/records/find returns only records matching user's role.

    Non-superusers see only records whose RecordType.role_name matches their roles.
    Records assigned to other roles should not appear.
    """
    # Assign record_role_a to user_with_role_a
    record_role_a.user_id = user_with_role_a.id
    test_session.add(record_role_a)
    await test_session.commit()

    response = await role_a_client.post(
        "/api/records/find",
        json={"user_id": str(user_with_role_a.id)},
    )
    assert response.status_code == 200
    data = response.json()["items"]

    # Should see assigned record_role_a; record_role_b is a different role
    assert len(data) == 1
    assert data[0]["id"] == record_role_a.id


@pytest.mark.asyncio
async def test_role_filtering_prevents_access_to_other_records(
    role_a_client, record_role_a, record_role_b
):
    """Role filtering prevents access to records with different roles.

    This test verifies the core RBAC behavior: a user with role_a can only
    access records with RecordType.role_name='role_a_test', and gets 403
    when trying to access records with different role assignments.
    """
    # User with role_a can access record_role_a
    response = await role_a_client.get(f"/api/records/{record_role_a.id}")
    assert response.status_code == 200

    # User with role_a cannot access record_role_b (different role)
    response = await role_a_client.get(f"/api/records/{record_role_b.id}")
    assert response.status_code == 403

    # Verify role_a user can update their own record
    response = await role_a_client.patch(
        f"/api/records/{record_role_a.id}/status",
        params={"record_status": "inwork"},
    )
    assert response.status_code == 200

    # Verify update succeeded
    get_response = await role_a_client.get(f"/api/records/{record_role_a.id}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "inwork"


@pytest.mark.asyncio
async def test_create_record_as_non_superuser(
    role_a_client, test_patient, test_study, test_series, record_type_role_a
):
    """Non-superuser can create records (role filtering happens on read/update, not create)."""
    response = await role_a_client.post(
        "/api/records/",  # Trailing slash required
        json={
            "patient_id": test_patient.id,
            "study_uid": test_study.study_uid,
            "series_uid": test_series.series_uid,
            "record_type_name": record_type_role_a.name,
            "status": "pending",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["record_type"]["name"] == "rtype-role-a-test"


@pytest.mark.asyncio
async def test_my_records_includes_unassigned_matching_role(
    test_session,
    role_a_client,
    user_with_role_a,
    record_role_a,
    test_patient,
    test_study,
    test_series,
    record_type_role_a,
):
    """POST /api/records/find includes both assigned and unassigned records matching the user's role."""
    # Assign record_role_a to user
    record_role_a.user_id = user_with_role_a.id
    test_session.add(record_role_a)
    await test_session.commit()

    # Create a second unassigned record with same role
    unassigned_record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        record_type_name=record_type_role_a.name,
        record_type=record_type_role_a,
        status="pending",
        user_id=None,
    )
    test_session.add(unassigned_record)
    await test_session.commit()
    await test_session.refresh(unassigned_record)

    response = await role_a_client.post("/api/records/find", json={})
    assert response.status_code == 200
    data = response.json()["items"]

    record_ids = {r["id"] for r in data}
    assert record_role_a.id in record_ids
    assert unassigned_record.id in record_ids
    assert len(data) == 2


@pytest.mark.asyncio
async def test_my_records_excludes_other_user_assigned_records(
    test_session,
    role_a_client,
    user_with_role_a,
    record_role_a,
    test_patient,
    test_study,
    test_series,
    record_type_role_a,
):
    """POST /api/records/find with wo_user=True excludes records assigned to any user."""
    # Create another user
    other_user_id = uuid4()
    other_user = User(
        id=other_user_id,
        email="other_user@test.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(other_user)
    await test_session.commit()

    # Assign record_role_a to the other user
    record_role_a.user_id = other_user_id
    test_session.add(record_role_a)
    await test_session.commit()

    response = await role_a_client.post("/api/records/find", json={"wo_user": True})
    assert response.status_code == 200
    data = response.json()["items"]

    # The record is assigned to another user, should not appear with wo_user=True
    record_ids = {r["id"] for r in data}
    assert record_role_a.id not in record_ids


# Admin-role access tests (Solution 1: AdminUserDep accepts is_superuser OR 'admin' role)


@pytest.mark.asyncio
async def test_admin_endpoint_admin_role_user_ok(admin_role_client):
    """Non-superuser with the 'admin' role can access /api/admin/* endpoints."""
    response = await admin_role_client.get("/api/admin/stats")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_endpoint_random_role_user_403(role_a_client):
    """Non-superuser with a non-admin role still gets 403 from admin endpoints."""
    response = await role_a_client.get("/api/admin/stats")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reports_endpoint_admin_role_user_ok(admin_role_client):
    """Admin-role user can list custom SQL reports."""
    response = await admin_role_client.get("/api/admin/reports")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_studies_endpoint_admin_role_user_ok(admin_role_client):
    """Admin-role user can list studies (router-level admin gate on study.py)."""
    response = await admin_role_client.get("/api/studies")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_patients_endpoint_admin_role_user_ok(admin_role_client):
    """Admin-role user can list patients (router-level admin gate on study.py)."""
    response = await admin_role_client.get("/api/patients")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_users_endpoint_admin_role_user_ok(admin_role_client):
    """Admin-role user can list users (per-endpoint admin gate on user.py)."""
    response = await admin_role_client.get("/api/user/")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_user_read_includes_role_names(admin_role_client):
    """`UserRead` exposes ``role_names`` so the SPA can drive admin gates."""
    response = await admin_role_client.get("/api/user/me")
    assert response.status_code == 200
    body = response.json()
    assert "role_names" in body
    assert "admin" in body["role_names"]


@pytest.mark.asyncio
async def test_user_read_role_names_empty_for_no_roles(superuser_client):
    """Superuser without explicit roles gets an empty ``role_names`` list."""
    response = await superuser_client.get("/api/user/me")
    assert response.status_code == 200
    body = response.json()
    assert body["role_names"] == []
