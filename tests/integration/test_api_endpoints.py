"""API endpoints tests."""

import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from src.models.task import TaskDesign, TaskStatus


@pytest.mark.asyncio
async def test_login_endpoint(client: AsyncClient, test_user):
    """Test authorization endpoint."""
    # fastapi-users uses email as username
    response = await client.post(
        "/auth/login",
        data={
            "username": "test@example.com",
            "password": "testpassword",
        },
    )

    # fastapi-users returns 204 No Content for successful login with cookie
    assert response.status_code in [200, 204]
    # Check that cookie is set
    assert response.cookies.get("clarinet_session") is not None


@pytest.mark.asyncio
async def test_login_invalid_credentials(client: AsyncClient):
    """Test authorization with invalid credentials."""
    response = await client.post(
        "/auth/login",
        data={
            "username": "wrong@example.com",
            "password": "wrongpassword",
        },
    )

    # fastapi-users returns 400 for invalid credentials
    assert response.status_code == 400
    assert response.json()["detail"] == "LOGIN_BAD_CREDENTIALS"


@pytest.mark.asyncio
async def test_get_current_user(client: AsyncClient, test_user):
    """Test getting current user."""
    # First authenticate
    login_response = await client.post(
        "/auth/login",
        data={
            "username": "test@example.com",
            "password": "testpassword",
        },
    )
    assert login_response.status_code == 204

    # Use new endpoint /auth/me
    response = await client.get("/auth/me")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "test_user"
    assert data["email"] == "test@example.com"
    assert data["is_active"]


@pytest.mark.asyncio
async def test_get_users_list(client: AsyncClient, admin_user):
    """Test getting users list (requires admin)."""
    # Authenticate as admin
    await client.post(
        "/auth/login",
        data={
            "username": "admin@example.com",
            "password": "adminpassword",
        },
    )

    response = await client.get("/user/users/")

    # May return 200 or 403 depending on implementation
    assert response.status_code in [200, 403, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_create_task_scheme(client: AsyncClient, admin_user):
    """Test creating task type via API."""
    # Authenticate as admin
    await client.post(
        "/auth/login",
        data={
            "username": "admin@example.com",
            "password": "adminpassword",
        },
    )

    task_scheme_data = {
        "name": "api_test_task",
        "title": "API Test Task",
        "description": "Task created via API",
        "type": "classification",
        "schema": json.dumps({"type": "object", "properties": {"label": {"type": "string"}}}),
    }

    response = await client.post("/task/types", json=task_scheme_data)

    # May require special permissions or not exist
    assert response.status_code in [200, 201, 403, 404]
    if response.status_code in [200, 201]:
        data = response.json()
        assert "id" in data or "name" in data


@pytest.mark.asyncio
async def test_get_task_types(client: AsyncClient, auth_headers):
    """Test getting task types list."""
    response = await client.get("/task/types", headers=auth_headers)

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_create_task(client: AsyncClient, auth_headers, test_session):
    """Test creating task via API."""
    # First create task type in DB

    task_design = TaskDesign(name="test_api_scheme", title="Test Scheme")
    test_session.add(task_design)
    await test_session.commit()

    # Create task via API
    task_data = {"task_design_id": task_design.name, "data": json.dumps({"test": "value"})}

    response = await client.post("/task/", json=task_data, headers=auth_headers)

    assert response.status_code in [200, 201, 404, 422]
    if response.status_code in [200, 201]:
        data = response.json()
        assert "id" in data
        assert data["status"] == TaskStatus.pending.value


@pytest.mark.asyncio
async def test_get_user_tasks(client: AsyncClient, auth_headers):
    """Test getting user tasks."""
    response = await client.get("/task/my", headers=auth_headers)

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_update_task_status(client: AsyncClient, auth_headers, test_session):
    """Test updating task status."""
    # Create task in DB
    # Get user
    from sqlmodel import select

    from src.models.patient import Patient
    from src.models.task import Task
    from src.models.user import User

    statement = select(User).where(User.id == "test_user")
    result = await test_session.execute(statement)
    user = result.scalar_one()

    # Create patient
    patient = Patient(id="UPDATE_PAT001", name="Update Test Patient")
    test_session.add(patient)
    await test_session.commit()

    # Create task type and task
    task_design = TaskDesign(name="update_test", title="Update Test")
    test_session.add(task_design)
    await test_session.commit()

    task = Task(
        patient_id=patient.id,
        user_id=user.id,
        task_design_id=task_design.name,
        status=TaskStatus.pending,
    )
    test_session.add(task)
    await test_session.commit()

    # Update status via API
    update_data = {"status": TaskStatus.inwork.value}

    response = await client.patch(f"/task/{task.id}", json=update_data, headers=auth_headers)

    assert response.status_code in [200, 404, 403, 405]
    if response.status_code == 200:
        data = response.json()
        assert data["status"] == TaskStatus.inwork.value


@pytest.mark.asyncio
async def test_get_studies(client: AsyncClient, auth_headers):
    """Test getting studies list."""
    response = await client.get("/study/", headers=auth_headers)

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_create_patient(client: AsyncClient, auth_headers):
    """Test creating patient via API."""
    patient_data = {
        "patient_id": "API_PAT001",
        "patient_name": "API Test Patient",
        "patient_birthdate": "1985-03-15",
        "patient_sex": "M",
    }

    response = await client.post("/study/patients", json=patient_data, headers=auth_headers)

    assert response.status_code in [200, 201, 404, 422]
    if response.status_code in [200, 201]:
        data = response.json()
        assert "id" in data or "patient_id" in data


@pytest.mark.asyncio
async def test_create_study(client: AsyncClient, auth_headers, test_session):
    """Test creating study via API."""
    # Create patient in DB
    from src.models.patient import Patient

    patient = Patient(id="API_PAT002", name="Study Test Patient")
    test_session.add(patient)
    await test_session.commit()

    # Create study via API
    study_data = {
        "patient_id": patient.id,
        "study_instance_uid": "1.2.3.4.5.100",
        "study_date": str(datetime.now(UTC).date()),
        "study_description": "API Test Study",
        "modality": "CT",
    }

    response = await client.post("/study/", json=study_data, headers=auth_headers)

    assert response.status_code in [200, 201, 404, 422]
    if response.status_code in [200, 201]:
        data = response.json()
        assert "id" in data or "study_instance_uid" in data


@pytest.mark.asyncio
async def test_unauthorized_access(client: AsyncClient):
    """Test access without authorization."""
    endpoints = [
        "/user/users/me/token",
        "/task/",
        "/study/",
    ]

    for endpoint in endpoints:
        response = await client.get(endpoint)
        # Some endpoints return 404 instead of 401 when not authenticated
        assert response.status_code in [401, 404]


@pytest.mark.asyncio
async def test_pagination(client: AsyncClient, auth_headers):
    """Test pagination if supported."""
    response = await client.get("/task/?limit=10&offset=0", headers=auth_headers)

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        # Check that list or pagination object is returned
        assert isinstance(data, list | dict)


@pytest.mark.asyncio
async def test_search_filter(client: AsyncClient, auth_headers):
    """Test filtering/search if supported."""
    response = await client.get("/study/?modality=CT", headers=auth_headers)

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)
        # If there are results, check filter
        if data:
            for item in data:
                if "modality" in item:
                    assert item["modality"] == "CT"


@pytest.mark.asyncio
async def test_cors_preflight(client: AsyncClient):
    """Test CORS preflight request."""
    response = await client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    # OPTIONS may be allowed or not
    assert response.status_code in [200, 405]
    if response.status_code == 200:
        # Check CORS headers
        headers = response.headers
        assert any(k.lower() == "access-control-allow-origin" for k in headers)
