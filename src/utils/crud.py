"""
CRUD utilities for the Clarinet framework.

This module provides common functions for Create, Read, Update, and Delete
operations with SQLModel models, as well as validation utilities for JSON data.
"""

from typing import (
    Any,
    Dict,
    Type,
    TypeVar,
)

from fastapi import HTTPException, status
from jsonschema import validate, ValidationError, SchemaError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

from ..exceptions import CONFLICT, NOT_FOUND
from ..utils.logger import logger

# Type variable for SQLModel subclasses
ModelT = TypeVar("ModelT", bound=SQLModel)


def add_item(item: ModelT, session: Session) -> ModelT:
    """
    Add a new item to the database.

    Args:
        item: The SQLModel instance to add
        session: Database session

    Returns:
        The added item with refreshed data

    Raises:
        HTTPException: If item with the same ID already exists or other integrity errors
    """
    try:
        session.add(item)
        session.commit()
        session.refresh(item)
    except IntegrityError as e:
        session.rollback()
        logger.error(f"Failed to add {item.__class__.__name__}: {e}")
        raise CONFLICT.with_context(
            f"{item.__class__.__name__} with this ID already exists in database"
        )
    return item


def get_item(model_class: Type[ModelT], item_id: Any, session: Session) -> ModelT:
    """
    Retrieve an item by ID.

    Args:
        model_class: The SQLModel class
        item_id: The primary key value
        session: Database session

    Returns:
        The retrieved item

    Raises:
        HTTPException: If item not found
    """
    item = session.get(model_class, item_id)
    if item is None:
        raise NOT_FOUND.with_context(
            f"{model_class.__name__} with ID {item_id} not found"
        )
    return item


def update_item(item: ModelT, update_data: Dict[str, Any], session: Session) -> ModelT:
    """
    Update an item with new data.

    Args:
        item: The item to update
        update_data: Dictionary of fields to update
        session: Database session

    Returns:
        The updated item

    Raises:
        HTTPException: If update fails
    """
    try:
        for field, value in update_data.items():
            setattr(item, field, value)
        session.add(item)
        session.commit()
        session.refresh(item)
    except IntegrityError as e:
        session.rollback()
        logger.error(f"Failed to update {item.__class__.__name__}: {e}")
        raise CONFLICT.with_context(
            f"Update failed due to integrity constraint: {str(e)}"
        )
    return item


def delete_item(item: SQLModel, session: Session) -> None:
    """
    Delete an item from the database.

    Args:
        item: The item to delete
        session: Database session

    Raises:
        HTTPException: If deletion fails
    """
    try:
        session.delete(item)
        session.commit()
    except IntegrityError as e:
        session.rollback()
        logger.error(f"Failed to delete {item.__class__.__name__}: {e}")
        raise CONFLICT.with_context(f"Cannot delete due to related items: {str(e)}")


def validate_json_by_schema(json_data: Any, json_schema: Dict[str, Any]) -> bool:
    """
    Validate JSON data against a JSON schema.

    Args:
        json_data: The data to validate
        json_schema: The schema to validate against

    Returns:
        True if validation succeeds

    Raises:
        HTTPException: If validation fails
    """
    try:
        validate(instance=json_data, schema=json_schema)
    except ValidationError as e:
        logger.error(f"JSON validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"JSON validation failed: {str(e)}",
        )
    except SchemaError as e:
        logger.error(f"JSON schema error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON schema: {str(e)}",
        )
    return True


def get_sql_type_from_value(value: Any) -> Any:
    """
    Get the appropriate SQLAlchemy type for a value.

    This is useful for dynamic type casting in ORM operations.

    Args:
        value: The value to determine type for

    Returns:
        SQLAlchemy type

    Raises:
        NotImplementedError: If type cannot be determined
    """
    from sqlalchemy import Boolean, Float, Integer, String

    match value:
        case bool():
            return Boolean
        case int():
            return Integer
        case float():
            return Float
        case str():
            return String
        case None:
            return String
        case _:
            raise NotImplementedError(f"Cannot determine SQL type for value: {value}")
