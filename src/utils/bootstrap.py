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
from src.utils.auth import get_password_hash
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


async def create_admin_user(
    username: str | None = None,
    email: str | None = None,
    password: str | None = None,
) -> User | None:
    """
    Create a default administrator user if it doesn't exist.

    Args:
        username: Admin username (defaults to settings.admin_username)
        email: Admin email (defaults to settings.admin_email)
        password: Admin password (defaults to settings.admin_password)

    Returns:
        The created or existing admin user, None if creation disabled

    Raises:
        ValueError: If password is not configured and required
    """
    from src.settings import settings

    # Check if admin creation is enabled
    if not settings.admin_auto_create:
        logger.info("Admin auto-creation is disabled")
        return None

    # Use settings defaults if not provided
    username = username or settings.admin_username
    email = email or settings.admin_email
    password = password or settings.admin_password

    # Validate password is configured
    if not password:
        if settings.debug:
            # In debug mode, use a default password with warning
            password = "admin123"
            logger.warning(
                "SECURITY WARNING: Using default admin password 'admin123'. "
                "Configure CLARINET_ADMIN_PASSWORD for production!"
            )
        else:
            raise ValueError(
                "Admin password not configured. Set CLARINET_ADMIN_PASSWORD "
                "environment variable or admin_password in settings."
            )

    # Validate password strength if required
    if settings.admin_require_strong_password:
        if len(password) < 12:
            raise ValueError("Admin password must be at least 12 characters in production")
        if not any(c.isupper() for c in password):
            raise ValueError("Admin password must contain uppercase letters")
        if not any(c.islower() for c in password):
            raise ValueError("Admin password must contain lowercase letters")
        if not any(c.isdigit() for c in password):
            raise ValueError("Admin password must contain numbers")

    async with db_manager.get_async_session_context() as session:
        # Check if admin user already exists
        existing_result = await session.execute(select(User).where(User.id == username))
        existing_user = existing_result.scalar_one_or_none()

        if existing_user:
            logger.info(f"Admin user '{username}' already exists")

            # Ensure user has superuser privileges
            if not existing_user.is_superuser:
                existing_user.is_superuser = True
                existing_user.is_active = True
                existing_user.is_verified = True
                await session.commit()
                logger.info(f"Updated user '{username}' to superuser")

            return existing_user

        # Create new admin user
        hashed_password = get_password_hash(password)
        admin_user = User(
            id=username,
            email=email,
            hashed_password=hashed_password,
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )

        session.add(admin_user)
        await session.commit()
        await session.refresh(admin_user)

        # Assign admin role if it exists
        role_result = await session.execute(select(UserRole).where(UserRole.name == "admin"))
        admin_role = role_result.scalar_one_or_none()
        if admin_role:
            admin_user.roles.append(admin_role)
            await session.commit()
            logger.info(f"Assigned 'admin' role to user '{username}'")

        logger.info(f"Created admin user '{username}' with email '{email}'")

        if settings.debug and password == "admin123":
            logger.warning(
                "⚠️  DEFAULT ADMIN CREDENTIALS IN USE!\n"
                "   Username: admin\n"
                "   Password: admin123\n"
                "   CHANGE THESE IMMEDIATELY!"
            )

        return admin_user


async def initialize_application_data() -> None:
    """
    Initialize application with default data including roles and admin user.

    This replaces the direct call to add_default_user_roles in CLI.
    """
    from src.settings import settings

    # Create default roles
    await add_default_user_roles()

    # Create admin user
    try:
        await create_admin_user()
    except ValueError as e:
        logger.error(f"Failed to create admin user: {e}")
        if not settings.debug:
            raise


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
