"""
Async task router for the Clarinet framework.

This module provides async API endpoints for managing tasks, task types, and task submissions.
"""

import random
from collections.abc import Sequence

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    status,
)
from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy import String as SQLString
from sqlalchemy import cast, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_, col, select

from src.api.dependencies import (
    common_parameters,
    get_current_user_async,
    get_current_user_cookie_async,
)
from src.exceptions import CONFLICT, NOT_FOUND
from src.models import (
    Patient,
    Series,
    Study,
    Task,
    TaskCreate,
    TaskDesign,
    TaskDesignCreate,
    TaskDesignFind,
    TaskDesignOptional,
    TaskFindResult,
    TaskFindResultComparisonOperator,
    TaskRead,
    TaskStatus,
    User,
    UserRole,
)
from src.types import PaginationParams, TaskResult
from src.utils.async_crud import (
    add_item_async,
    exists_async,
)
from src.utils.database import get_async_session
from src.utils.logger import logger
from src.utils.validation import validate_json_by_schema

router = APIRouter(
    prefix="/tasks",
    tags=["Tasks"],
    responses={
        404: {"description": "Not found"},
        409: {"description": "Conflict"},
    },
)


# Task Type Endpoints


@router.get("/types", response_model=list[TaskDesign])
async def get_all_task_designs(
    session: AsyncSession = Depends(get_async_session),
) -> Sequence[TaskDesign]:
    """Get all task types."""
    result = await session.execute(select(TaskDesign))
    return result.scalars().all()


@router.post("/types/find", response_model=list[TaskDesign])
async def find_task_design(
    find_query: TaskDesignFind,
    session: AsyncSession = Depends(get_async_session),
) -> Sequence[TaskDesign]:
    """Find task types by criteria."""
    find_terms = find_query.model_dump(exclude_none=True)
    find_statement = select(TaskDesign)

    for find_key, find_value in find_terms.items():
        if find_key == "name":
            find_statement = find_statement.where(
                cast(TaskDesign.name, SQLString).like(f"%{find_value}%")
            )
        elif isinstance(find_value, list):
            find_statement = find_statement.where(getattr(TaskDesign, find_key) == find_value)
        else:
            find_statement = find_statement.where(getattr(TaskDesign, find_key) == find_value)

    result = await session.execute(find_statement)
    return result.scalars().all()


@router.post("/types", response_model=TaskDesign, status_code=status.HTTP_201_CREATED)
async def add_task_design(
    task_design: TaskDesignCreate,
    constrain_unique_names: bool = True,
    session: AsyncSession = Depends(get_async_session),
) -> TaskDesign:
    """Create a new task type."""
    new_task_design = TaskDesign.model_validate(task_design)

    # Validate result schema if present
    if new_task_design.result_schema is not None:
        try:
            Draft202012Validator.check_schema(new_task_design.result_schema)
        except SchemaError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Result schema is invalid",
            ) from e

    # Ensure task type name is unique if required
    if constrain_unique_names and await exists_async(TaskDesign, session, name=task_design.name):
        raise CONFLICT.with_context(f"There is already a task type with name '{task_design.name}'")

    return await add_item_async(new_task_design, session)


@router.patch("/types/{task_design_id}", response_model=TaskDesign)
async def update_task_design(
    task_design_id: int,
    task_design_update: TaskDesignOptional,
    session: AsyncSession = Depends(get_async_session),
) -> TaskDesign:
    """Update an existing task type."""
    task_design = await session.get(TaskDesign, task_design_id)
    if task_design is None:
        raise NOT_FOUND.with_context(f"Task type with ID {task_design_id} not found")

    # Validate result schema if present
    if task_design_update.result_schema is not None:
        try:
            Draft202012Validator.check_schema(task_design_update.result_schema)
        except SchemaError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Result schema is invalid",
            ) from e

    # Update fields
    update_data = task_design_update.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in update_data.items():
        setattr(task_design, field, value)

    await session.commit()
    await session.refresh(task_design)
    return task_design


@router.get("/types/{task_design_id}", response_model=TaskDesign)
async def get_task_design(
    task_design_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> TaskDesign:
    """Get a task type by ID."""
    task_design = await session.get(TaskDesign, task_design_id)
    if task_design is None:
        raise NOT_FOUND.with_context(f"Task type with ID {task_design_id} not found")
    return task_design


@router.delete("/types/{task_design_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task_design(
    task_design_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Delete a task type."""
    task_design = await session.get(TaskDesign, task_design_id)
    if task_design is None:
        raise NOT_FOUND.with_context(f"Task type with ID {task_design_id} not found")

    await session.delete(task_design)
    await session.commit()


# Task Endpoints


@router.get("/", response_model=list[Task])
async def get_all_tasks(session: AsyncSession = Depends(get_async_session)) -> Sequence[Task]:
    """Get all tasks."""
    result = await session.execute(select(Task))
    return result.scalars().all()


@router.get("/my", response_model=list[Task])
async def get_my_tasks(
    user: User = Depends(get_current_user_cookie_async),
    session: AsyncSession = Depends(get_async_session),
) -> Sequence[Task]:
    """Get all tasks assigned to the current user."""
    result = await session.execute(select(Task).where(Task.user_id == user.id))
    return result.scalars().all()


@router.get("/my/pending", response_model=Sequence[TaskRead])
async def get_my_pending_tasks(
    user: User = Depends(get_current_user_cookie_async),
    session: AsyncSession = Depends(get_async_session),
) -> Sequence[Task]:
    """Get all pending tasks assigned to the current user."""
    result = await session.execute(
        select(Task).where(
            Task.user_id == user.id,
            and_(
                Task.status != TaskStatus.failed,
                Task.status != TaskStatus.finished,
                Task.status != TaskStatus.pause,
            ),
        )
    )
    return result.scalars().all()


@router.get("/available_types", response_model=dict[TaskDesign, int])
async def get_my_available_task_designs(
    user: User = Depends(get_current_user_cookie_async),
    session: AsyncSession = Depends(get_async_session),
) -> dict[TaskDesign, int]:
    """Get all task types available to the current user with task counts."""
    statement = (
        select(TaskDesign.name, func.count(col(Task.id)).label("task_count"))
        .join(Task)
        .join(UserRole)
        .where(UserRole.users.any(User.id == user.id))  # type: ignore[attr-defined]
        .where(Task.status == TaskStatus.pending)
        .group_by(col(TaskDesign.name))
    )
    result = await session.execute(statement)
    results = result.all()  # This returns tuples (id, count), not scalars

    return {
        task_design: task_count
        for task_design_id, task_count in results
        if (task_design := await session.get(TaskDesign, task_design_id)) is not None
    }


@router.get("/{task_id}", response_model=Task | TaskRead)
async def get_task(
    task_id: int,
    detailed: bool = False,
    session: AsyncSession = Depends(get_async_session),
) -> Task | TaskRead:
    """Get a task by ID."""
    task = await session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    if detailed:
        return TaskRead.model_validate(task)
    return task


async def check_task_constraints(
    new_task: TaskCreate,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Check if a task can be added based on constraints."""
    # Count existing tasks with same task type, series, and study
    query = (
        select(func.count(col(Task.id)))
        .join(TaskDesign)
        .where(
            TaskDesign.name == new_task.task_design_id,
            Task.series_uid == new_task.series_uid,
            Task.study_uid == new_task.study_uid,
        )
    )

    result = await session.execute(query)
    same_tasks_count = result.scalar_one()
    task_design = await session.get(TaskDesign, new_task.task_design_id)

    if task_design is None:
        raise NOT_FOUND.with_context(f"Task type with ID {new_task.task_design_id} not found")

    if task_design.max_users and same_tasks_count >= task_design.max_users:
        raise CONFLICT.with_context(
            f"The maximum users per task limit \
            ({same_tasks_count} of {task_design.max_users})\
            is reached"
        )


@router.post(
    "/",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(check_task_constraints)],
)
async def add_task(
    new_task: TaskCreate,
    session: AsyncSession = Depends(get_async_session),
) -> Task:
    """Create a new task."""
    task = Task(**new_task.model_dump())
    session.add(task)
    await session.commit()
    await session.refresh(task)

    # Publish event or trigger background tasks here if needed

    return task


@router.patch("/{task_id}/status", response_model=Task)
async def update_task_status(
    task_id: int,
    task_status: TaskStatus,
    session: AsyncSession = Depends(get_async_session),
) -> Task:
    """Update a task's status."""
    task = await session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    task.status = task_status
    await session.commit()
    await session.refresh(task)
    return task


@router.patch("/{task_id}/user", response_model=Task)
async def assign_task_to_user(
    task_id: int,
    user_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> Task:
    """Assign a task to a user."""
    task = await session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    user = await session.get(User, user_id)
    if user is None:
        raise NOT_FOUND.with_context(f"User with ID {user_id} not found")

    task.user_id = user_id
    await session.commit()
    await session.refresh(task)
    return task


async def validate_task_result(
    task_id: int,
    result: TaskResult,
    session: AsyncSession = Depends(get_async_session),
) -> TaskResult:
    """Validate a task result against its schema."""
    task = await session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    # Load task_design relationship
    await session.refresh(task, ["task_design"])

    # Validate against task type's result schema
    if task.task_design.result_schema:
        try:
            validate_json_by_schema(result, task.task_design.result_schema)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Result does not match schema: {e!s}",
            ) from e

    # Add additional validation here (e.g., Slicer validation)

    return result


@router.post("/{task_id}/result", response_model=TaskRead)
async def submit_task_result(
    task_id: int,
    result: TaskResult,
    session: AsyncSession = Depends(get_async_session),
) -> Task:
    """Submit a result for a task."""
    # Get and validate task
    task = await session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    if task.status == TaskStatus.finished:
        raise CONFLICT.with_context("Task already finished. Use PATCH to update the task result.")

    # Validate result
    validated_result = await validate_task_result(task_id, result, session)

    # Update task
    task.result = validated_result
    task.status = TaskStatus.finished
    await session.commit()
    await session.refresh(task)

    # Publish event or trigger background tasks here if needed

    return task


@router.patch("/{task_id}/result", response_model=TaskRead)
async def update_task_result(
    task_id: int,
    result: TaskResult,
    session: AsyncSession = Depends(get_async_session),
) -> Task:
    """Update a task's result."""
    # Get and validate task
    task = await session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    if task.status != TaskStatus.finished:
        raise CONFLICT.with_context("Task is not finished yet. Use POST to submit a task result.")

    # Validate result
    validated_result = await validate_task_result(task_id, result, session)

    # Update task
    task.result = validated_result
    await session.commit()
    await session.refresh(task)

    # Publish event or trigger background tasks here if needed

    return task


@router.post("/find", response_model=list[TaskRead])
async def find_tasks(
    find_queries: list[TaskFindResult] = Body(default=[]),
    patient_id: str | None = None,
    patient_anon_id: str | None = None,
    series_uid: str | None = None,
    anon_series_uid: str | None = None,
    study_uid: str | None = None,
    anon_study_uid: str | None = None,
    user_id: str | None = None,
    task_name: str | None = None,
    task_status: TaskStatus | None = None,
    wo_user: bool | None = None,
    random_one: bool = False,
    session: AsyncSession = Depends(get_async_session),
    commons: PaginationParams = Depends(common_parameters),
) -> Sequence[Task]:
    """Find tasks by various criteria."""
    find_statement = select(Task).join(TaskDesign)

    # Add filters for patient
    if patient_id:
        find_statement = find_statement.join(Study).join(Patient).where(Patient.id == patient_id)

    if (
        patient_anon_id and "_" in patient_anon_id
    ):  # Extract auto_id from anon_id format (e.g., "CLARINET_123" -> 123)
        auto_id = int(patient_anon_id.split("_")[1])
        find_statement = find_statement.join(Study).join(Patient).where(Patient.auto_id == auto_id)

    # Add filters for series
    if series_uid:
        find_statement = find_statement.where(Task.series_uid == series_uid)

    match anon_series_uid:
        case None:
            pass
        case "Null":
            find_statement = find_statement.join(Series).where(Series.anon_uid is None)
        case "*":
            find_statement = find_statement.join(Series).where(Series.anon_uid is not None)
        case _:
            find_statement = find_statement.join(Series).where(Series.anon_uid == anon_series_uid)

    # Add filters for study
    if study_uid:
        find_statement = find_statement.where(Task.study_uid == study_uid)

    match anon_study_uid:
        case None:
            pass
        case "Null":
            find_statement = find_statement.join(Study).where(Study.anon_uid is None)
        case "*":
            find_statement = find_statement.join(Study).where(Study.anon_uid is not None)
        case _:
            find_statement = find_statement.join(Study).where(Study.anon_uid == anon_study_uid)

    # Add user filters
    match wo_user:
        case None:
            pass
        case True:
            find_statement = find_statement.where(Task.user_id is None)
        case False:
            find_statement = find_statement.where(Task.user_id is not None)

    if user_id:
        find_statement = find_statement.where(Task.user_id == user_id)

    # Add task filters
    if task_status:
        find_statement = find_statement.where(Task.status == task_status)

    if task_name:
        find_statement = find_statement.where(TaskDesign.name == task_name)

    # Add result filters
    for query in find_queries:
        # Task.result is a JSON column, we need to handle it properly
        result_field = Task.result.op("->")(query.result_name).as_string()  # type: ignore[union-attr]
        match query.comparison_operator:
            case TaskFindResultComparisonOperator.eq:
                find_statement = find_statement.where(
                    result_field.cast(query.sql_type) == query.result_value
                )
            case TaskFindResultComparisonOperator.gt:
                find_statement = find_statement.where(
                    result_field.cast(query.sql_type) > query.result_value
                )
            case TaskFindResultComparisonOperator.lt:
                find_statement = find_statement.where(
                    result_field.cast(query.sql_type) < query.result_value
                )
            case TaskFindResultComparisonOperator.contains:
                find_statement = find_statement.where(
                    result_field.cast(query.sql_type).like(f"%{query.result_value}%")
                )

    # Apply pagination
    find_statement = find_statement.distinct()
    if commons.get("skip"):
        find_statement = find_statement.offset(commons["skip"])
    if commons.get("limit"):
        find_statement = find_statement.limit(commons["limit"])

    # Execute query
    result = await session.execute(find_statement)
    results = result.scalars().all()

    # Apply random selection if requested
    if random_one and results:
        results = [random.choice(results)]

    logger.info(f"Found {len(results)} tasks matching criteria")
    return results


@router.patch("/bulk/status", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_update_task_status(
    task_ids: list[int],
    new_status: TaskStatus,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Update status for multiple tasks at once."""
    for task_id in task_ids:
        task = await session.get(Task, task_id)
        if task:
            task.status = new_status

    await session.commit()


async def assign_user_to_task(
    task_id: int,
    user: User = Depends(get_current_user_async),
    session: AsyncSession = Depends(get_async_session),
) -> Task:
    """Assign the current user to a task."""
    task = await session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    task.status = TaskStatus.inwork
    task.user_id = user.id
    await session.commit()
    await session.refresh(task)
    return task


async def get_random_series_async(session: AsyncSession = Depends(get_async_session)) -> Series:
    """Get a random series from the database."""
    result = await session.execute(select(Series))
    all_series = result.scalars().all()
    if not all_series:
        raise NOT_FOUND.with_context("No series found in database")
    return random.choice(all_series)


async def add_demo_tasks_for_user(
    user: User,
    series: Series = Depends(get_random_series_async),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Add demo tasks for a new user."""
    # Find demo task types
    result = await session.execute(
        select(TaskDesign).where(cast(TaskDesign.name, SQLString).like("%demo%"))
    )
    task_designs = result.scalars().all()

    if not task_designs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No demo task types found",
        )

    # Create a task for each demo task type
    for task_design in task_designs:
        if task_design.level == "SERIES":
            new_task = TaskCreate(
                status=TaskStatus.pending,
                user_id=user.id,
                series_uid=series.series_uid,
                study_uid=series.study_uid,
                patient_id=series.study.patient_id,
                task_design_id=task_design.name,
            )
        elif task_design.level == "STUDY":
            new_task = TaskCreate(
                status=TaskStatus.pending,
                user_id=user.id,
                study_uid=series.study_uid,
                patient_id=series.study.patient_id,
                task_design_id=task_design.name,
            )
        else:
            continue

        task = Task(**new_task.model_dump())
        session.add(task)

    await session.commit()
