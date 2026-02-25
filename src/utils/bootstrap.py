"""
Bootstrap utilities for Clarinet application initialization.

This module provides functions to initialize the application with default data,
such as user roles and record types, during startup.
"""

import json
import os
from typing import Any

import aiofiles
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from src.models import RecordType, RecordTypeCreate, User, UserRole
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
        # Eagerly load roles relationship to avoid lazy loading in async context
        users_result = await session.execute(select(User).options(selectinload(User.roles)))  # type:ignore[arg-type]
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
        existing_result = await session.execute(select(User).where(User.email == email))
        existing_user = existing_result.scalar_one_or_none()

        if existing_user:
            logger.info(f"Admin user with email '{email}' already exists")

            # Ensure user has superuser privileges
            if not existing_user.is_superuser:
                existing_user.is_superuser = True
                existing_user.is_active = True
                existing_user.is_verified = True
                await session.commit()
                logger.info(f"Updated user with email '{email}' to superuser")

            return existing_user

        # Create new admin user
        hashed_password = get_password_hash(password)
        admin_user = User(
            email=email,
            hashed_password=hashed_password,
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )

        session.add(admin_user)
        await session.commit()

        # Refresh with eager loading of roles to avoid lazy loading in async context
        admin_user_result = await session.execute(
            select(User).options(selectinload(User.roles)).where(User.id == admin_user.id)  # type:ignore[arg-type]
        )
        admin_user = admin_user_result.scalar_one()

        # Assign admin role if it exists
        role_result = await session.execute(select(UserRole).where(UserRole.name == "admin"))
        admin_role = role_result.scalar_one_or_none()
        if admin_role:
            admin_user.roles.append(admin_role)
            await session.commit()
            logger.info(f"Assigned 'admin' role to user with email '{email}'")

        logger.info(f"Created admin user with email '{email}'")

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


def filter_record_schemas(record_files: list[str], filter_suffix: str = "demo") -> list[str]:
    """
    Filter record schema files by suffix.

    Args:
        record_files: List of record file names
        filter_suffix: Suffix to filter by

    Returns:
        List of record names (without .json extension)
    """
    logger.info(f"Record type files found: {', '.join(record_files)}")
    filtered_by_suffix = filter(lambda x: filter_suffix in x, record_files)
    record_names = [t.removesuffix(".json") for t in filtered_by_suffix if "schema" not in t]
    return record_names


async def _load_record_properties(input_folder: str, record_name: str) -> dict[str, Any] | None:
    """Load record type properties from JSON, resolving schema if needed.

    Args:
        input_folder: Path to the folder containing record type JSON files.
        record_name: Name of the record type (without .json extension).

    Returns:
        Properties dict, or None if schema could not be found.
    """
    async with aiofiles.open(os.path.join(input_folder, f"{record_name}.json")) as f:
        content = await f.read()
        props: dict[str, Any] = json.loads(content)

    if props.get("data_schema") is not None:
        return props

    # Legacy: rename result_schema → data_schema
    if props.get("result_schema") is not None:
        props["data_schema"] = props.pop("result_schema")
        return props

    # Try loading from separate schema file
    try:
        async with aiofiles.open(os.path.join(input_folder, f"{record_name}.schema.json")) as f:
            content = await f.read()
            props["data_schema"] = json.loads(content)
        return props
    except FileNotFoundError:
        logger.warning(f"Cannot find schema for record type {record_name}!")
        return None


async def _upsert_record_type(props: dict[str, Any], session: AsyncSession) -> None:
    """Create a record type, logging conflicts as info.

    Args:
        props: Record type properties dict.
        session: Database session.
    """
    new_record_type = RecordTypeCreate(**props)
    try:
        await add_record_type(new_record_type, session=session)
        logger.info(f"Created record type: {props.get('name')}")
    except HTTPException as e:
        if e.status_code == status.HTTP_409_CONFLICT:
            logger.info(f"Record type already exists: {props.get('name')}")
        else:
            logger.error(f"Error creating record type {props.get('name')}: {e}")


async def create_demo_record_types_from_json(input_folder: str, demo_suffix: str = "demo") -> None:
    """
    Create record types from JSON files in the specified folder.

    Args:
        input_folder: Path to the folder containing record type JSON files
        demo_suffix: Suffix to filter record type files by
    """
    try:
        record_files = os.listdir(input_folder)
    except FileNotFoundError:
        logger.warning(f"Record types folder {input_folder} not found")
        return

    record_names = filter_record_schemas(record_files, demo_suffix)
    logger.info(f"Found record type schemas: {record_names}")

    for record_name in record_names:
        async with db_manager.get_async_session_context() as session:
            try:
                props = await _load_record_properties(input_folder, record_name)
                if props is None:
                    continue
                await _upsert_record_type(props, session)
            except Exception as e:
                logger.error(f"Error processing record type {record_name}: {e}")


async def add_record_type(record_type: RecordTypeCreate, session: AsyncSession) -> RecordType:
    """
    Add a new record type to the database.

    Args:
        record_type: The record type to add
        session: Database session

    Returns:
        The created record type

    Raises:
        HTTPException: If the record type already exists
    """
    # Check if record type with this name already exists
    existing_result = await session.execute(
        select(RecordType).where(RecordType.name == record_type.name)
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Record type with name {record_type.name} already exists",
        )

    # Validate data schema if provided
    if record_type.data_schema is not None:
        try:
            # In a real implementation, you might want to validate the schema
            # using a library like jsonschema
            pass
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Data schema is invalid: {e}",
            ) from e

    # Create and save the record type
    new_record_type = RecordType.model_validate(record_type)
    session.add(new_record_type)
    await session.commit()
    await session.refresh(new_record_type)

    return new_record_type
