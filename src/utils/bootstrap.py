"""
Bootstrap utilities for Clarinet application initialization.

This module provides functions to initialize the application with default data,
such as user roles and task types, during startup.
"""

import json
import os

import aiofiles
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.models import TaskDesign, TaskDesignCreate, User, UserRole
from src.utils.db_manager import db_manager
from src.utils.logger import logger


async def add_default_user_roles() -> None:
    """
    Add default user roles to the database if they don't exist.

    Default roles include: doctor, auto, admin, expert, ordinator
    """
    default_roles = ["doctor", "auto", "admin", "expert", "ordinator"]

    async with db_manager.get_async_session_context() as session:
        for role_name in default_roles:
            try:
                await create_user_role(role_name, session=session)
                logger.info(f"Created role: {role_name}")
            except HTTPException as e:
                if e.status_code == status.HTTP_409_CONFLICT:
                    logger.info(f"Role already exists: {role_name}")
                    continue
                else:
                    raise


async def give_role_to_all_users(role_name: str) -> None:
    """
    Assign a role to all users in the database.

    Args:
        role_name: The name of the role to assign
    """
    async with db_manager.get_async_session_context() as session:
        users_result = await session.execute(select(User))
        users = users_result.scalars().all()

        role_result = await session.execute(select(UserRole).where(UserRole.name == role_name))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role with name: {role_name} was not found!",
            )

        for user in users:
            try:
                if role not in user.roles:
                    user.roles.append(role)
                    logger.info(f"Assigned role {role_name} to user {user.id}")
            except HTTPException as e:
                if e.status_code == status.HTTP_409_CONFLICT:
                    logger.info(f"User {user.id} already has role {role_name}")
                    await session.rollback()
                    continue
                else:
                    raise

        await session.commit()


async def create_user_role(role_name: str, session: AsyncSession) -> UserRole:
    """
    Create a new user role if it doesn't exist.

    Args:
        role_name: The name of the role to create
        session: Database session

    Returns:
        The created or existing role

    Raises:
        HTTPException: If the role already exists
    """
    existing_result = await session.execute(select(UserRole).where(UserRole.name == role_name))
    existing = existing_result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role with name {role_name} already exists",
        )

    new_role = UserRole(name=role_name)
    session.add(new_role)
    await session.commit()
    await session.refresh(new_role)
    return new_role


def filter_task_schemas(task_files: list[str], filter_suffix: str = "demo") -> list[str]:
    """
    Filter task schema files by suffix.

    Args:
        task_files: List of task file names
        filter_suffix: Suffix to filter by

    Returns:
        List of task names (without .json extension)
    """
    logger.info(f"Task files found: {', '.join(task_files)}")
    filtered_by_suffix = filter(lambda x: filter_suffix in x, task_files)
    task_names = [t.removesuffix(".json") for t in filtered_by_suffix if "schema" not in t]
    return task_names


async def create_demo_task_designs_from_json(input_folder: str, demo_suffix: str = "demo") -> None:
    """
    Create task types from JSON files in the specified folder.

    Args:
        input_folder: Path to the folder containing task JSON files
        demo_suffix: Suffix to filter task files by
    """
    try:
        task_files = os.listdir(input_folder)
    except FileNotFoundError:
        logger.warning(f"Task folder {input_folder} not found")
        return

    task_names = filter_task_schemas(task_files, demo_suffix)
    logger.info(f"Found task schemas: {task_names}")

    for task_name in task_names:
        async with db_manager.get_async_session_context() as session:
            try:
                # Load task properties
                async with aiofiles.open(os.path.join(input_folder, f"{task_name}.json")) as f:
                    content = await f.read()
                    task_properties = json.loads(content)

                # Load task schema if it exists
                if task_properties.get("result_schema") is None:
                    try:
                        async with aiofiles.open(
                            os.path.join(input_folder, f"{task_name}.schema.json")
                        ) as f:
                            content = await f.read()
                            task_scheme_json = json.loads(content)
                        task_properties["result_schema"] = task_scheme_json
                    except FileNotFoundError:
                        logger.warning(f"Cannot find schema for task {task_name}!")
                        continue

                # Create task type
                new_task_design = TaskDesignCreate(**task_properties)
                try:
                    await add_task_design(new_task_design, session=session)
                    logger.info(f"Created task type: {task_name}")
                except HTTPException as e:
                    if e.status_code == status.HTTP_409_CONFLICT:
                        logger.info(f"Task type already exists: {task_name}")
                    else:
                        logger.error(f"Error creating task type {task_name}: {e}")
            except Exception as e:
                logger.error(f"Error processing task {task_name}: {e}")


async def add_task_design(task_design: TaskDesignCreate, session: AsyncSession) -> TaskDesign:
    """
    Add a new task type to the database.

    Args:
        task_design: The task type to add
        session: Database session

    Returns:
        The created task type

    Raises:
        HTTPException: If the task type already exists
    """
    # Check if task type with this name already exists
    existing_result = await session.execute(
        select(TaskDesign).where(TaskDesign.name == task_design.name)
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task type with name {task_design.name} already exists",
        )

    # Validate result schema if provided
    if task_design.result_schema is not None:
        try:
            # In a real implementation, you might want to validate the schema
            # using a library like jsonschema
            pass
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Result schema is invalid: {e}",
            ) from e

    # Create and save the task type
    new_task_design = TaskDesign.model_validate(task_design)
    session.add(new_task_design)
    await session.commit()
    await session.refresh(new_task_design)

    return new_task_design
