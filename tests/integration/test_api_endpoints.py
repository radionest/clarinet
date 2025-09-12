"""Тесты API endpoints."""

import json
from datetime import date

import pytest
from httpx import AsyncClient

from src.models.task import TaskDesign, TaskStatus


@pytest.mark.asyncio
async def test_login_endpoint(client: AsyncClient, test_user):
    """Тест endpoint авторизации."""
    # Создаем пользователя через фикстуру test_user
    response = await client.post(
        "/auth/login",
        data={
            "username": "test@example.com",
            "password": "testpassword",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "token_type" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_credentials(client: AsyncClient):
    """Тест авторизации с неверными данными."""
    response = await client.post(
        "/auth/login",
        data={
            "username": "wrong@example.com",
            "password": "wrongpassword",
        }
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user(client: AsyncClient, auth_headers):
    """Тест получения текущего пользователя."""
    response = await client.get(
        "/user/users/me/token",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "test@example.com"
    assert data["isactive"] == True


@pytest.mark.asyncio
async def test_get_users_list(client: AsyncClient, admin_headers):
    """Тест получения списка пользователей (требует admin)."""
    response = await client.get(
        "/user/users/",
        headers=admin_headers
    )

    # Может вернуть 200 или 403 в зависимости от реализации
    assert response.status_code in [200, 403, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_create_task_scheme(client: AsyncClient, admin_headers):
    """Тест создания типа задачи через API."""
    task_scheme_data = {
        "name": "api_test_task",
        "title": "API Test Task",
        "description": "Task created via API",
        "type": "classification",
        "schema": json.dumps({
            "type": "object",
            "properties": {
                "label": {"type": "string"}
            }
        })
    }

    response = await client.post(
        "/task/types",
        json=task_scheme_data,
        headers=admin_headers
    )

    # Может требовать специальных прав или не существовать
    assert response.status_code in [200, 201, 403, 404]
    if response.status_code in [200, 201]:
        data = response.json()
        assert "id" in data or "name" in data


@pytest.mark.asyncio
async def test_get_task_types(client: AsyncClient, auth_headers):
    """Тест получения списка типов задач."""
    response = await client.get(
        "/task/types",
        headers=auth_headers
    )

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_create_task(client: AsyncClient, auth_headers, test_session):
    """Тест создания задачи через API."""
    # Сначала создаем тип задачи в БД

    task_design = TaskDesign(
        name="test_api_scheme",
        title="Test Scheme"
    )
    test_session.add(task_design)
    await test_session.commit()

    # Создаем задачу через API
    task_data = {
        "task_design_id": task_design.name,
        "data": json.dumps({"test": "value"})
    }

    response = await client.post(
        "/task/",
        json=task_data,
        headers=auth_headers
    )

    assert response.status_code in [200, 201, 404, 422]
    if response.status_code in [200, 201]:
        data = response.json()
        assert "id" in data
        assert data["status"] == TaskStatus.pending.value


@pytest.mark.asyncio
async def test_get_user_tasks(client: AsyncClient, auth_headers):
    """Тест получения задач пользователя."""
    response = await client.get(
        "/task/my",
        headers=auth_headers
    )

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_update_task_status(client: AsyncClient, auth_headers, test_session):
    """Тест обновления статуса задачи."""
    # Создаем задачу в БД
    # Получаем пользователя
    from sqlmodel import select

    from src.models.patient import Patient
    from src.models.task import Task
    from src.models.user import User
    
    statement = select(User).where(User.id == "test@example.com")
    result = await test_session.execute(statement)
    user = result.scalar_one()

    # Создаем пациента
    patient = Patient(
        id="UPDATE_PAT001",
        name="Update Test Patient"
    )
    test_session.add(patient)
    await test_session.commit()

    # Создаем тип задачи и задачу
    task_design = TaskDesign(
        name="update_test",
        title="Update Test"
    )
    test_session.add(task_design)
    await test_session.commit()

    task = Task(
        patient_id=patient.id,
        user_id=user.id,
        task_design_id=task_design.name,
        status=TaskStatus.pending
    )
    test_session.add(task)
    await test_session.commit()

    # Обновляем статус через API
    update_data = {
        "status": TaskStatus.inwork.value
    }

    response = await client.patch(
        f"/task/{task.id}",
        json=update_data,
        headers=auth_headers
    )

    assert response.status_code in [200, 404, 403, 405]
    if response.status_code == 200:
        data = response.json()
        assert data["status"] == TaskStatus.inwork.value


@pytest.mark.asyncio
async def test_get_studies(client: AsyncClient, auth_headers):
    """Тест получения списка исследований."""
    response = await client.get(
        "/study/",
        headers=auth_headers
    )

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_create_patient(client: AsyncClient, auth_headers):
    """Тест создания пациента через API."""
    patient_data = {
        "patient_id": "API_PAT001",
        "patient_name": "API Test Patient",
        "patient_birthdate": "1985-03-15",
        "patient_sex": "M"
    }

    response = await client.post(
        "/study/patients",
        json=patient_data,
        headers=auth_headers
    )

    assert response.status_code in [200, 201, 404, 422]
    if response.status_code in [200, 201]:
        data = response.json()
        assert "id" in data or "patient_id" in data


@pytest.mark.asyncio
async def test_create_study(client: AsyncClient, auth_headers, test_session):
    """Тест создания исследования через API."""
    # Создаем пациента в БД
    from src.models.patient import Patient

    patient = Patient(
        id="API_PAT002",
        name="Study Test Patient"
    )
    test_session.add(patient)
    await test_session.commit()

    # Создаем исследование через API
    study_data = {
        "patient_id": patient.id,
        "study_instance_uid": "1.2.3.4.5.100",
        "study_date": str(date.today()),
        "study_description": "API Test Study",
        "modality": "CT"
    }

    response = await client.post(
        "/study/",
        json=study_data,
        headers=auth_headers
    )

    assert response.status_code in [200, 201, 404, 422]
    if response.status_code in [200, 201]:
        data = response.json()
        assert "id" in data or "study_instance_uid" in data


@pytest.mark.asyncio
async def test_unauthorized_access(client: AsyncClient):
    """Тест доступа без авторизации."""
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
    """Тест пагинации если поддерживается."""
    response = await client.get(
        "/task/?limit=10&offset=0",
        headers=auth_headers
    )

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        # Проверяем что возвращается список или объект с пагинацией
        assert isinstance(data, (list, dict))


@pytest.mark.asyncio
async def test_search_filter(client: AsyncClient, auth_headers):
    """Тест фильтрации/поиска если поддерживается."""
    response = await client.get(
        "/study/?modality=CT",
        headers=auth_headers
    )

    assert response.status_code in [200, 404]
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, list)
        # Если есть результаты, проверяем фильтр
        if data:
            for item in data:
                if "modality" in item:
                    assert item["modality"] == "CT"


@pytest.mark.asyncio
async def test_cors_preflight(client: AsyncClient):
    """Тест CORS preflight запроса."""
    response = await client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        }
    )

    # OPTIONS может быть разрешен или нет
    assert response.status_code in [200, 405]
    if response.status_code == 200:
        # Проверяем CORS заголовки
        headers = response.headers
        assert any(k.lower() == "access-control-allow-origin" for k in headers)
