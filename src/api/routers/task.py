"""
Task router for the Clarinet framework.

This module provides API endpoints for managing tasks, task types, and task submissions.
"""

import random
from typing import Dict, List, Optional, Sequence, Union

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    status,
)
from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy import func
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select, or_, and_

from src.exceptions import CONFLICT, NOT_FOUND
from src.models import (
    DicomQueryLevel,
    Patient,
    Series,
    Study,
    Task,
    TaskCreate,
    TaskFindResult,
    TaskFindResultComparisonOperator,
    TaskRead,
    TaskStatus,
    TaskType,
    TaskTypeCreate,
    TaskTypeFind,
    TaskTypeOptional,
    User,
    UserRole,
)
from src.api.routers.user import get_current_user, get_current_user_cookie
from src.api.routers.study import get_random_series
from src.utils.database import get_session
from src.utils.logger import logger
from src.utils.crud import (
    add_item,
    common_parameters,
    get_item,
    sql_type_from_val,
    validate_json_by_scheme,
)

router = APIRouter(
    prefix="/tasks",
    tags=["Tasks"],
    responses={
        404: {"description": "Not found"},
        409: {"description": "Conflict"},
    },
)


# Task Type Endpoints


@router.get("/types", response_model=List[TaskType])
async def get_all_task_types(
    session: Session = Depends(get_session),
) -> Sequence[TaskType]:
    """Get all task types."""
    return session.exec(select(TaskType)).all()


@router.post("/types/find", response_model=List[TaskType])
async def find_task_type(
    find_query: TaskTypeFind,
    session: Session = Depends(get_session),
) -> Sequence[TaskType]:
    """Find task types by criteria."""
    find_terms = find_query.model_dump(exclude_none=True)
    find_statement = select(TaskType)

    for find_key, find_value in find_terms.items():
        if find_key == "name":
            find_statement = find_statement.where(TaskType.name.contains(find_value))
        elif isinstance(find_value, list):
            find_statement = find_statement.where(
                getattr(TaskType, find_key) == find_value
            )
        else:
            find_statement = find_statement.where(
                getattr(TaskType, find_key) == find_value
            )

    return session.exec(find_statement).all()


@router.post("/types", response_model=TaskType, status_code=status.HTTP_201_CREATED)
async def add_task_type(
    task_type: TaskTypeCreate,
    constrain_unique_names: bool = True,
    session: Session = Depends(get_session),
) -> TaskType:
    """Create a new task type."""
    new_task_type = TaskType.model_validate(task_type)

    # Validate result schema if present
    if new_task_type.result_schema is not None:
        try:
            Draft202012Validator.check_schema(new_task_type.result_schema)
        except SchemaError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Result schema is invalid",
            )

    # Ensure task type name is unique if required
    if constrain_unique_names:
        existing = session.exec(
            select(TaskType).where(TaskType.name == task_type.name)
        ).first()
        if existing is not None:
            raise CONFLICT.with_context(
                f"There is already a task type with name '{task_type.name}'"
            )

    return add_item(new_task_type, session)


@router.patch("/types/{task_type_id}", response_model=TaskType)
async def update_task_type(
    task_type_id: int,
    task_type_update: TaskTypeOptional,
    session: Session = Depends(get_session),
) -> TaskType:
    """Update an existing task type."""
    task_type = session.get(TaskType, task_type_id)
    if task_type is None:
        raise NOT_FOUND.with_context(f"Task type with ID {task_type_id} not found")

    # Validate result schema if present
    if task_type_update.result_schema is not None:
        try:
            Draft202012Validator.check_schema(task_type_update.result_schema)
        except SchemaError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Result schema is invalid",
            )

    # Update fields
    update_data = task_type_update.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in update_data.items():
        setattr(task_type, field, value)

    session.commit()
    session.refresh(task_type)
    return task_type


@router.get("/types/{task_type_id}", response_model=TaskType)
async def get_task_type(
    task_type_id: int,
    session: Session = Depends(get_session),
) -> TaskType:
    """Get a task type by ID."""
    task_type = session.get(TaskType, task_type_id)
    if task_type is None:
        raise NOT_FOUND.with_context(f"Task type with ID {task_type_id} not found")
    return task_type


@router.delete("/types/{task_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task_type(
    task_type_id: int,
    session: Session = Depends(get_session),
) -> None:
    """Delete a task type."""
    task_type = session.get(TaskType, task_type_id)
    if task_type is None:
        raise NOT_FOUND.with_context(f"Task type with ID {task_type_id} not found")

    session.delete(task_type)
    session.commit()


# Task Endpoints


@router.get("/", response_model=List[Task])
async def get_all_tasks(session: Session = Depends(get_session)) -> Sequence[Task]:
    """Get all tasks."""
    return session.exec(select(Task)).all()


@router.get("/my", response_model=List[Task])
async def get_my_tasks(
    user: User = Depends(get_current_user_cookie),
    session: Session = Depends(get_session),
) -> Sequence[Task]:
    """Get all tasks assigned to the current user."""
    return session.exec(select(Task).where(Task.user_id == user.id)).all()


@router.get("/my/pending", response_model=Sequence[TaskRead])
async def get_my_pending_tasks(
    user: User = Depends(get_current_user_cookie),
    session: Session = Depends(get_session),
) -> Sequence[Task]:
    """Get all pending tasks assigned to the current user."""
    return session.exec(
        select(Task).where(
            Task.user_id == user.id,
            Task.status.not_in(
                [TaskStatus.failed, TaskStatus.finished, TaskStatus.pause]
            ),
        )
    ).all()


@router.get("/available_types", response_model=Dict[TaskType, int])
async def get_my_available_task_types(
    user: User = Depends(get_current_user_cookie),
    session: Session = Depends(get_session),
) -> Dict[TaskType, int]:
    """Get all task types available to the current user with task counts."""
    statement = (
        select(TaskType.id, func.count(Task.id).label("task_count"))
        .join(Task)
        .join(UserRole)
        .where(UserRole.users.contains(user))
        .where(Task.status == TaskStatus.pending)
        .group_by(TaskType.id)
    )
    results = session.exec(statement).all()

    return {
        task_type: task_count
        for task_type_id, task_count in results
        if (task_type := session.get(TaskType, task_type_id)) is not None
    }


@router.get("/{task_id}", response_model=Union[Task, TaskRead])
async def get_task(
    task_id: int,
    detailed: bool = False,
    session: Session = Depends(get_session),
) -> Union[Task, TaskRead]:
    """Get a task by ID."""
    task = session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    if detailed:
        return TaskRead.model_validate(task)
    return task


async def check_task_constraints(
    new_task: TaskCreate,
    session: Session = Depends(get_session),
) -> None:
    """Check if a task can be added based on constraints."""
    # Count existing tasks with same task type, series, and study
    query = (
        select(func.count(Task.id))
        .join(TaskType)
        .where(
            TaskType.id == new_task.task_type_id,
            Task.series_uid == new_task.series_uid,
            Task.study_uid == new_task.study_uid,
        )
    )

    same_tasks_count = session.exec(query).one()
    task_type = session.get(TaskType, new_task.task_type_id)

    if task_type is None:
        raise NOT_FOUND.with_context(
            f"Task type with ID {new_task.task_type_id} not found"
        )

    if task_type.max_users and same_tasks_count >= task_type.max_users:
        raise CONFLICT.with_context(
            f"The maximum users per task limit \
            ({same_tasks_count} of {task_type.max_users})\
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
    session: Session = Depends(get_session),
) -> Task:
    """Create a new task."""
    task = Task(**new_task.model_dump())
    session.add(task)
    session.commit()
    session.refresh(task)

    # Publish event or trigger background tasks here if needed

    return task


@router.patch("/{task_id}/status", response_model=Task)
async def update_task_status(
    task_id: int,
    task_status: TaskStatus,
    session: Session = Depends(get_session),
) -> Task:
    """Update a task's status."""
    task = session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    task.status = task_status
    session.commit()
    session.refresh(task)
    return task


@router.patch("/{task_id}/user", response_model=Task)
async def assign_task_to_user(
    task_id: int,
    user_id: str,
    session: Session = Depends(get_session),
) -> Task:
    """Assign a task to a user."""
    task = session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    user = session.get(User, user_id)
    if user is None:
        raise NOT_FOUND.with_context(f"User with ID {user_id} not found")

    task.user_id = user_id
    session.commit()
    session.refresh(task)
    return task


async def validate_task_result(
    task_id: int,
    result: Dict,
    session: Session = Depends(get_session),
) -> Dict:
    """Validate a task result against its schema."""
    task = session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    # Validate against task type's result schema
    if task.task_type.result_schema:
        try:
            validate_json_by_scheme(result, task.task_type.result_schema)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Result does not match schema: {str(e)}",
            )

    # Add additional validation here (e.g., Slicer validation)

    return result


@router.post("/{task_id}/result", response_model=TaskRead)
async def submit_task_result(
    task_id: int,
    result: Dict,
    session: Session = Depends(get_session),
) -> Task:
    """Submit a result for a task."""
    # Get and validate task
    task = session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    if task.status == TaskStatus.finished:
        raise CONFLICT.with_context(
            "Task already finished. Use PATCH to update the task result."
        )

    # Validate result
    validated_result = await validate_task_result(task_id, result, session)

    # Update task
    task.result = validated_result
    task.status = TaskStatus.finished
    session.commit()
    session.refresh(task)

    # Publish event or trigger background tasks here if needed

    return task


@router.patch("/{task_id}/result", response_model=TaskRead)
async def update_task_result(
    task_id: int,
    result: Dict,
    session: Session = Depends(get_session),
) -> Task:
    """Update a task's result."""
    # Get and validate task
    task = session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    if task.status != TaskStatus.finished:
        raise CONFLICT.with_context(
            "Task is not finished yet. Use POST to submit a task result."
        )

    # Validate result
    validated_result = await validate_task_result(task_id, result, session)

    # Update task
    task.result = validated_result
    session.commit()
    session.refresh(task)

    # Publish event or trigger background tasks here if needed

    return task


@router.post("/find", response_model=List[TaskRead])
async def find_tasks(
    find_queries: List[TaskFindResult] = Body(default=[]),
    patient_id: Optional[str] = None,
    patient_anon_id: Optional[str] = None,
    series_uid: Optional[str] = None,
    anon_series_uid: Optional[str] = None,
    study_uid: Optional[str] = None,
    anon_study_uid: Optional[str] = None,
    user_id: Optional[str] = None,
    task_name: Optional[str] = None,
    task_status: Optional[TaskStatus] = None,
    wo_user: Optional[bool] = None,
    random_one: bool = False,
    session: Session = Depends(get_session),
    commons: dict = Depends(common_parameters),
) -> Sequence[Task]:
    """Find tasks by various criteria."""
    find_statement = select(Task).join(TaskType)

    # Add filters for patient
    if patient_id:
        find_statement = (
            find_statement.join(Study).join(Patient).where(Patient.id == patient_id)
        )

    if patient_anon_id:
        find_statement = (
            find_statement.join(Study)
            .join(Patient)
            .where(Patient.anon_id == patient_anon_id)
        )

    # Add filters for series
    if series_uid:
        find_statement = find_statement.where(Task.series_uid == series_uid)

    match anon_series_uid:
        case None:
            pass
        case "Null":
            find_statement = find_statement.join(Series).where(Series.anon_uid == None)
        case "*":
            find_statement = find_statement.join(Series).where(Series.anon_uid != None)
        case _:
            find_statement = find_statement.join(Series).where(
                Series.anon_uid == anon_series_uid
            )

    # Add filters for study
    if study_uid:
        find_statement = find_statement.where(Task.study_uid == study_uid)

    match anon_study_uid:
        case None:
            pass
        case "Null":
            find_statement = find_statement.join(Study).where(Study.anon_uid == None)
        case "*":
            find_statement = find_statement.join(Study).where(Study.anon_uid != None)
        case _:
            find_statement = find_statement.join(Study).where(
                Study.anon_uid == anon_study_uid
            )

    # Add user filters
    match wo_user:
        case None:
            pass
        case True:
            find_statement = find_statement.where(Task.user_id == None)
        case False:
            find_statement = find_statement.where(Task.user_id != None)

    if user_id:
        find_statement = find_statement.where(Task.user_id == user_id)

    # Add task filters
    if task_status:
        find_statement = find_statement.where(Task.status == task_status)

    if task_name:
        find_statement = find_statement.where(TaskType.name == task_name)

    # Add result filters
    for query in find_queries:
        match query.comparison_operator:
            case TaskFindResultComparisonOperator.eq:
                find_statement = find_statement.where(
                    Task.result[query.result_name].as_string().cast(query.sql_type)
                    == query.result_value
                )
            case TaskFindResultComparisonOperator.gt:
                find_statement = find_statement.where(
                    Task.result[query.result_name].as_string().cast(query.sql_type)
                    > query.result_value
                )
            case TaskFindResultComparisonOperator.lt:
                find_statement = find_statement.where(
                    Task.result[query.result_name].as_string().cast(query.sql_type)
                    < query.result_value
                )
            case TaskFindResultComparisonOperator.contains:
                find_statement = find_statement.where(
                    Task.result[query.result_name]
                    .as_string()
                    .cast(query.sql_type)
                    .contains(query.result_value)
                )

    # Apply pagination
    find_statement = find_statement.distinct(Task.id)
    if commons.get("skip"):
        find_statement = find_statement.offset(commons["skip"])
    if commons.get("limit"):
        find_statement = find_statement.limit(commons["limit"])

    # Execute query
    results = session.exec(find_statement).all()

    # Apply random selection if requested
    if random_one and results:
        results = [random.choice(results)]

    logger.info(f"Found {len(results)} tasks matching criteria")
    return results


@router.patch("/bulk/status", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_update_task_status(
    task_ids: List[int],
    new_status: TaskStatus,
    session: Session = Depends(get_session),
) -> None:
    """Update status for multiple tasks at once."""
    for task_id in task_ids:
        task = session.get(Task, task_id)
        if task:
            task.status = new_status

    session.commit()


def assign_user_to_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Task:
    """Assign the current user to a task."""
    task = session.get(Task, task_id)
    if task is None:
        raise NOT_FOUND.with_context(f"Task with ID {task_id} not found")

    task.status = TaskStatus.inwork
    task.user_id = user.id
    session.commit()
    session.refresh(task)
    return task


def add_demo_tasks_for_user(
    user: User,
    series: Series = Depends(get_random_series),
    session: Session = Depends(get_session),
) -> None:
    """Add demo tasks for a new user."""
    # Find demo task types
    task_types = session.exec(
        select(TaskType).where(TaskType.name.contains("demo"))
    ).all()

    if not task_types:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No demo task types found",
        )

    # Create a task for each demo task type
    for task_type in task_types:
        if task_type.level == DicomQueryLevel.series:
            new_task = TaskCreate(
                status=TaskStatus.pending,
                user_id=user.id,
                series_uid=series.series_uid,
                study_uid=series.study_uid,
                task_type_id=task_type.id,
            )
        elif task_type.level == DicomQueryLevel.study:
            new_task = TaskCreate(
                status=TaskStatus.pending,
                user_id=user.id,
                study_uid=series.study_uid,
                task_type_id=task_type.id,
            )
        else:
            continue

        task = Task(**new_task.model_dump())
        session.add(task)

    session.commit()
