"""
Bootstrap utilities for Clarinet application initialization.

This module provides functions to initialize the application with default data,
such as user roles and task types, during startup.
"""

import os
import json
from typing import List, Optional, Dict, Any
from pathlib import Path

from sqlmodel import Session, select
from fastapi import HTTPException, status

from src.models import TaskType, TaskTypeCreate, UserRole, User
from src.utils.database import get_session_context
from src.utils.logger import logger
from src.settings import settings


def add_default_user_roles() -> None:
    """
    Add default user roles to the database if they don't exist.

    Default roles include: doctor, auto, admin, expert, ordinator
    """
    default_roles = ["doctor", "auto", "admin", "expert", "ordinator"]

    with get_session_context() as session:
        for role_name in default_roles:
            try:
                create_user_role(role_name, session=session)
                logger.info(f"Created role: {role_name}")
            except HTTPException as e:
                if e.status_code == status.HTTP_409_CONFLICT:
                    logger.info(f"Role already exists: {role_name}")
                    continue
                else:
                    raise


def give_role_to_all_users(role_name: str) -> None:
    """
    Assign a role to all users in the database.

    Args:
        role_name: The name of the role to assign
    """
    with get_session_context() as session:
        users = session.exec(select(User)).all()

        role = session.exec(select(UserRole).where(UserRole.name == role_name)).first()
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
                    session.rollback()
                    continue
                else:
                    raise

        session.commit()


def create_user_role(role_name: str, session: Session) -> UserRole:
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
    existing = session.exec(select(UserRole).where(UserRole.name == role_name)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role with name {role_name} already exists",
        )

    new_role = UserRole(name=role_name)
    session.add(new_role)
    session.commit()
    session.refresh(new_role)
    return new_role


def filter_task_schemas(
    task_files: List[str], filter_suffix: str = "demo"
) -> List[str]:
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
    task_names = [
        t.removesuffix(".json") for t in filtered_by_suffix if "schema" not in t
    ]
    return task_names


def create_demo_task_types_from_json(
    input_folder: str, demo_suffix: str = "demo"
) -> None:
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
        with get_session_context() as session:
            try:
                # Load task properties
                with open(os.path.join(input_folder, f"{task_name}.json")) as f:
                    task_properties = json.load(f)

                # Load task schema if it exists
                if task_properties.get("result_schema") is None:
                    try:
                        with open(
                            os.path.join(input_folder, f"{task_name}.schema.json")
                        ) as f:
                            task_scheme_json = json.load(f)
                        task_properties["result_schema"] = task_scheme_json
                    except FileNotFoundError:
                        logger.warning(f"Cannot find schema for task {task_name}!")
                        continue

                # Create task type
                new_task_type = TaskTypeCreate(**task_properties)
                try:
                    add_task_type(new_task_type, session=session)
                    logger.info(f"Created task type: {task_name}")
                except HTTPException as e:
                    if e.status_code == status.HTTP_409_CONFLICT:
                        logger.info(f"Task type already exists: {task_name}")
                    else:
                        logger.error(f"Error creating task type {task_name}: {e}")
            except Exception as e:
                logger.error(f"Error processing task {task_name}: {e}")


def add_task_type(task_type: TaskTypeCreate, session: Session) -> TaskType:
    """
    Add a new task type to the database.

    Args:
        task_type: The task type to add
        session: Database session

    Returns:
        The created task type

    Raises:
        HTTPException: If the task type already exists
    """
    # Check if task type with this name already exists
    existing = session.exec(
        select(TaskType).where(TaskType.name == task_type.name)
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task type with name {task_type.name} already exists",
        )

    # Validate result schema if provided
    if task_type.result_schema is not None:
        try:
            # In a real implementation, you might want to validate the schema
            # using a library like jsonschema
            pass
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Result schema is invalid: {e}",
            )

    # Create and save the task type
    new_task_type = TaskType.model_validate(task_type)
    session.add(new_task_type)
    session.commit()
    session.refresh(new_task_type)

    return new_task_type
