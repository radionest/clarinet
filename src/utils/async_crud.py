"""
Async CRUD utilities for the Clarinet framework.

This module provides asynchronous functions for Create, Read, Update, and Delete
operations with SQLModel models using AsyncSession.
"""

from collections.abc import Sequence
from typing import (
    Any,
    TypeVar,
)

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, select

from ..exceptions import CONFLICT, NOT_FOUND
from ..utils.logger import logger

# Type variable for SQLModel subclasses
ModelT = TypeVar("ModelT", bound=SQLModel)


async def add_item_async[ModelT: SQLModel](item: ModelT, session: AsyncSession) -> ModelT:
    """
    Add a new item to the database asynchronously.

    Args:
        item: The SQLModel instance to add
        session: Async database session

    Returns:
        The added item with refreshed data

    Raises:
        HTTPException: If item with the same ID already exists or other integrity errors
    """
    try:
        session.add(item)
        await session.commit()
        await session.refresh(item)
    except IntegrityError as e:
        await session.rollback()
        logger.error(f"Failed to add {item.__class__.__name__}: {e}")
        raise CONFLICT.with_context(
            f"{item.__class__.__name__} with this ID already exists in database"
        ) from e
    return item


async def get_item_async[ModelT: SQLModel](
    model_class: type[ModelT], item_id: Any, session: AsyncSession
) -> ModelT:
    """
    Retrieve an item by ID asynchronously.

    Args:
        model_class: The SQLModel class
        item_id: The primary key value
        session: Async database session

    Returns:
        The retrieved item

    Raises:
        HTTPException: If item not found
    """
    item = await session.get(model_class, item_id)
    if item is None:
        raise NOT_FOUND.with_context(f"{model_class.__name__} with ID {item_id} not found")
    return item


async def get_items_async[ModelT: SQLModel](
    model_class: type[ModelT],
    session: AsyncSession,
    skip: int = 0,
    limit: int | None = None,
    **filters: Any,
) -> Sequence[ModelT]:
    """
    Retrieve multiple items with optional filtering and pagination.

    Args:
        model_class: The SQLModel class
        session: Async database session
        skip: Number of items to skip
        limit: Maximum number of items to return
        **filters: Additional filter conditions

    Returns:
        List of items matching the criteria
    """
    statement = select(model_class)

    # Apply filters
    for field, value in filters.items():
        if hasattr(model_class, field):
            statement = statement.where(getattr(model_class, field) == value)

    # Apply pagination
    statement = statement.offset(skip)
    if limit is not None:
        statement = statement.limit(limit)

    result = await session.execute(statement)
    return result.scalars().all()


async def update_item_async[ModelT: SQLModel](
    item: ModelT, update_data: dict[str, Any], session: AsyncSession
) -> ModelT:
    """
    Update an item with new data asynchronously.

    Args:
        item: The item to update
        update_data: Dictionary of fields to update
        session: Async database session

    Returns:
        The updated item

    Raises:
        HTTPException: If update fails
    """
    try:
        for field, value in update_data.items():
            setattr(item, field, value)
        session.add(item)
        await session.commit()
        await session.refresh(item)
    except IntegrityError as e:
        await session.rollback()
        logger.error(f"Failed to update {item.__class__.__name__}: {e}")
        raise CONFLICT.with_context(f"Update failed due to integrity constraint: {e!s}") from e
    return item


async def delete_item_async(item: SQLModel, session: AsyncSession) -> None:
    """
    Delete an item from the database asynchronously.

    Args:
        item: The item to delete
        session: Async database session

    Raises:
        HTTPException: If deletion fails
    """
    try:
        await session.delete(item)
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        logger.error(f"Failed to delete {item.__class__.__name__}: {e}")
        raise CONFLICT.with_context(f"Cannot delete due to related items: {e!s}") from e


async def exists_async[ModelT: SQLModel](
    model_class: type[ModelT], session: AsyncSession, **filters: Any
) -> bool:
    """
    Check if an item exists with the given filters.

    Args:
        model_class: The SQLModel class
        session: Async database session
        **filters: Filter conditions

    Returns:
        True if item exists, False otherwise
    """
    statement = select(model_class)

    for field, value in filters.items():
        if hasattr(model_class, field):
            statement = statement.where(getattr(model_class, field) == value)

    statement = statement.limit(1)
    result = await session.execute(statement)
    return result.scalars().first() is not None


async def count_items_async[ModelT: SQLModel](
    model_class: type[ModelT], session: AsyncSession, **filters: Any
) -> int:
    """
    Count items matching the given filters.

    Args:
        model_class: The SQLModel class
        session: Async database session
        **filters: Filter conditions

    Returns:
        Number of items matching the criteria
    """
    from sqlalchemy import func

    statement = select(func.count()).select_from(model_class)

    for field, value in filters.items():
        if hasattr(model_class, field):
            statement = statement.where(getattr(model_class, field) == value)

    result = await session.execute(statement)
    return result.scalar_one()


async def get_or_create_async[ModelT: SQLModel](
    model_class: type[ModelT],
    session: AsyncSession,
    defaults: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[ModelT, bool]:
    """
    Get an existing item or create a new one if it doesn't exist.

    Args:
        model_class: The SQLModel class
        session: Async database session
        defaults: Default values for creation
        **kwargs: Fields to search by

    Returns:
        Tuple of (item, created) where created is True if item was created
    """
    statement = select(model_class)
    for field, value in kwargs.items():
        if hasattr(model_class, field):
            statement = statement.where(getattr(model_class, field) == value)

    result = await session.execute(statement)
    instance = result.scalars().first()

    if instance:
        return instance, False

    # Create new instance
    create_data = kwargs.copy()
    if defaults:
        create_data.update(defaults)

    instance = model_class(**create_data)
    session.add(instance)

    try:
        await session.commit()
        await session.refresh(instance)
        return instance, True
    except IntegrityError:
        await session.rollback()
        # Try to get again in case of race condition
        result = await session.execute(statement)
        instance = result.scalars().first()
        if instance:
            return instance, False
        raise
