"""Тесты CRUD операций для Task."""

from datetime import UTC, datetime

import pytest
from sqlmodel import select

from src.models.task import Task, TaskDesign, TaskStatus


@pytest.mark.asyncio
async def test_create_task_scheme(test_session):
    """Тест создания типа задачи (taskdesign)."""
    task_scheme = TaskDesign(
        name="Test Task Type",
        description="Test task description",
        result_schema={
            "type": "object",
            "properties": {
                "field1": {"type": "string"}
            }
        }
    )
    test_session.add(task_scheme)
    await test_session.commit()
    await test_session.refresh(task_scheme)

    assert task_scheme.name == "Test Task Type"
    assert task_scheme.description == "Test task description"
    assert task_scheme.result_schema is not None


@pytest.mark.asyncio
async def test_create_task(test_session, test_user, test_patient, test_study):
    """Тест создания задачи."""
    # Создаем тип задачи
    task_scheme = TaskDesign(
        name="Simple Task",
        description="Simple task",
        result_schema={"type": "object"}
    )
    test_session.add(task_scheme)
    await test_session.commit()

    # Создаем задачу
    task = Task(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        task_design_id=task_scheme.name,
        status=TaskStatus.pending
    )
    test_session.add(task)
    await test_session.commit()
    await test_session.refresh(task)

    assert task.id is not None
    assert task.user_id == test_user.id
    assert task.task_design_id == task_scheme.name
    assert task.status == TaskStatus.pending


@pytest.mark.asyncio
async def test_get_task_by_id(test_session, test_user, test_patient, test_study):
    """Тест получения задачи по ID."""
    # Создаем задачу
    task_scheme = TaskDesign(
        name="Get Task",
        description="Get task",
        result_schema={"type": "object"}
    )
    test_session.add(task_scheme)
    await test_session.commit()

    task = Task(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        task_design_id=task_scheme.name,
        status=TaskStatus.inwork
    )
    test_session.add(task)
    await test_session.commit()

    # Получаем задачу
    result = await test_session.get(Task, task.id)
    assert result is not None
    assert result.id == task.id
    assert result.status == TaskStatus.inwork


@pytest.mark.asyncio
async def test_update_task_status(test_session, test_user, test_patient, test_study):
    """Тест обновления статуса задачи."""
    # Создаем задачу
    task_scheme = TaskDesign(
        name="Update Task",
        description="Update task",
        result_schema={"type": "object"}
    )
    test_session.add(task_scheme)
    await test_session.commit()

    task = Task(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        task_design_id=task_scheme.name,
        status=TaskStatus.pending
    )
    test_session.add(task)
    await test_session.commit()

    # Обновляем статус
    task.status = TaskStatus.finished
    task.finished_at = datetime.now(UTC)
    test_session.add(task)
    await test_session.commit()
    await test_session.refresh(task)

    # Проверяем изменения
    updated_task = await test_session.get(Task, task.id)
    assert updated_task.status == TaskStatus.finished
    assert updated_task.finished_at is not None


@pytest.mark.asyncio
async def test_delete_task(test_session, test_user, test_patient, test_study):
    """Тест удаления задачи."""
    # Создаем задачу
    task_scheme = TaskDesign(
        name="Delete Task",
        description="Delete task",
        result_schema={"type": "object"}
    )
    test_session.add(task_scheme)
    await test_session.commit()

    task = Task(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        task_design_id=task_scheme.name,
        status=TaskStatus.pending
    )
    test_session.add(task)
    await test_session.commit()
    task_id = task.id

    # Удаляем задачу
    await test_session.delete(task)
    await test_session.commit()

    # Проверяем удаление
    deleted_task = await test_session.get(Task, task_id)
    assert deleted_task is None


@pytest.mark.asyncio
async def test_get_user_tasks(test_session, test_user, test_patient, test_study):
    """Тест получения задач пользователя."""
    # Создаем несколько задач для пользователя
    task_scheme = TaskDesign(
        name="User Tasks",
        description="User tasks",
        result_schema={"type": "object"}
    )
    test_session.add(task_scheme)
    await test_session.commit()

    for _ in range(3):
        task = Task(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            user_id=test_user.id,
            task_design_id=task_scheme.name,
            status=TaskStatus.pending
        )
        test_session.add(task)

    await test_session.commit()

    # Получаем задачи пользователя
    statement = select(Task).where(Task.user_id == test_user.id)
    result = await test_session.execute(statement)
    tasks = result.scalars().all()

    assert len(tasks) >= 3
    for task in tasks:
        assert task.user_id == test_user.id


@pytest.mark.asyncio
async def test_filter_tasks_by_status(test_session, test_user, test_patient, test_study):
    """Тест фильтрации задач по статусу."""
    # Создаем задачи с разными статусами
    task_scheme = TaskDesign(
        name="Filter Tasks",
        description="Filter tasks",
        result_schema={"type": "object"}
    )
    test_session.add(task_scheme)
    await test_session.commit()

    statuses = [TaskStatus.pending, TaskStatus.inwork, TaskStatus.finished]
    for status in statuses:
        task = Task(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            user_id=test_user.id,
            task_design_id=task_scheme.name,
            status=status
        )
        test_session.add(task)

    await test_session.commit()

    # Фильтруем по статусу PENDING
    statement = select(Task).where(
        (Task.user_id == test_user.id) &
        (Task.status == TaskStatus.pending)
    )
    result = await test_session.execute(statement)
    pending_tasks = result.scalars().all()

    assert len(pending_tasks) >= 1
    for task in pending_tasks:
        assert task.status == TaskStatus.pending


@pytest.mark.asyncio
async def test_task_scheme_with_multiple_tasks(test_session, test_user, admin_user):
    """Тест создания нескольких задач для одного типа."""
    # Создаем тип задачи
    task_scheme = TaskDesign(
        name="Shared Task Type",
        description="Shared task",
        result_schema={
            "type": "object",
            "properties": {
                "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]}
            }
        }
    )
    test_session.add(task_scheme)
    await test_session.commit()

    # Создаем необходимые объекты
    from src.models.patient import Patient
    from src.models.study import Study
    
    patient = Patient(
        id="TASK_PAT007",
        name="Multiple Tasks Patient",
        anon_name="ANON_TASK_007"
    )
    test_session.add(patient)
    await test_session.commit()
    
    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.TASK.7",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_TASK_STUDY_007"
    )
    test_session.add(study)
    await test_session.commit()
    
    # Создаем задачи для разных пользователей
    task1 = Task(
        patient_id=patient.id,
        study_uid=study.study_uid,
        user_id=test_user.id,
        task_design_id=task_scheme.name,
        status=TaskStatus.pending,
        result={"difficulty": "easy"}
    )
    task2 = Task(
        patient_id=patient.id,
        study_uid=study.study_uid,
        user_id=admin_user.id,
        task_design_id=task_scheme.name,
        status=TaskStatus.inwork,
        result={"difficulty": "hard"}
    )

    test_session.add(task1)
    test_session.add(task2)
    await test_session.commit()

    # Получаем все задачи этого типа
    statement = select(Task).where(Task.task_design_id == task_scheme.name)
    result = await test_session.execute(statement)
    tasks = result.scalars().all()

    assert len(tasks) == 2
    user_ids = [task.user_id for task in tasks]
    assert test_user.id in user_ids
    assert admin_user.id in user_ids


@pytest.mark.asyncio
async def test_task_data_json_field(test_session, test_user, test_patient, test_study):
    """Тест работы с JSON полем result в задаче."""
    # Создаем тип задачи с JSON схемой
    task_scheme = TaskDesign(
        name="JSON Task",
        description="JSON task",
        result_schema={
            "type": "object",
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "confidence": {"type": "number"}
            }
        }
    )
    test_session.add(task_scheme)
    await test_session.commit()

    # Создаем задачу с JSON данными
    task_data = {
        "labels": ["cat", "dog", "bird"],
        "confidence": 0.95
    }

    task = Task(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        task_design_id=task_scheme.name,
        status=TaskStatus.pending,
        result=task_data
    )
    test_session.add(task)
    await test_session.commit()
    await test_session.refresh(task)

    # Проверяем JSON данные
    stored_data = task.result or {}
    assert stored_data["labels"] == ["cat", "dog", "bird"]
    assert stored_data["confidence"] == 0.95
