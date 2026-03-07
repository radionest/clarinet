"""Repository for RecordType-specific database operations."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import String as SQLString
from sqlalchemy import cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.exceptions.domain import (
    RecordTypeAlreadyExistsError,
    RecordTypeNotFoundError,
    ValidationError,
)
from clarinet.models.file_schema import RecordTypeFileLink
from clarinet.models.record import RecordType, RecordTypeFind
from clarinet.repositories.base import BaseRepository
from clarinet.utils.graph_validation import detect_cycle


def _file_links_eager_load() -> list[Any]:
    """Return selectinload options for file_links → file_definition chain."""
    return [
        selectinload(RecordType.file_links).selectinload(RecordTypeFileLink.file_definition),  # type: ignore[arg-type]  # type: ignore[arg-type]
    ]


class RecordTypeRepository(BaseRepository[RecordType]):
    """Repository for RecordType model operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, RecordType)

    async def get(self, id: Any) -> RecordType:
        """Get record type by ID with file_links eagerly loaded.

        Args:
            id: Record type ID

        Returns:
            Found record type

        Raises:
            RecordTypeNotFoundError: If record type doesn't exist
        """
        statement = (
            select(RecordType).where(RecordType.name == id).options(*_file_links_eager_load())
        )
        result = await self.session.execute(statement)
        entity = result.scalars().first()
        if not entity:
            raise RecordTypeNotFoundError(id)
        return entity

    async def get_all(
        self, skip: int = 0, limit: int = 100, **filters: Any
    ) -> Sequence[RecordType]:
        """List record types with file_links eagerly loaded.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            **filters: Field-value pairs to filter by

        Returns:
            List of record types
        """
        statement = select(RecordType).options(*_file_links_eager_load())

        for field, value in filters.items():
            if hasattr(RecordType, field):
                statement = statement.where(getattr(RecordType, field) == value)

        statement = statement.offset(skip).limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_all(self, **filters: Any) -> Sequence[RecordType]:
        """List all record types with file_links eagerly loaded.

        Args:
            **filters: Field-value pairs to filter by

        Returns:
            List of all matching record types
        """
        statement = select(RecordType).options(*_file_links_eager_load())

        for field, value in filters.items():
            if hasattr(RecordType, field):
                statement = statement.where(getattr(RecordType, field) == value)

        result = await self.session.execute(statement)
        return result.scalars().all()

    async def find(self, criteria: RecordTypeFind) -> Sequence[RecordType]:
        """Find record types by criteria.

        Args:
            criteria: Search criteria with optional name, constraint_role, constraint_user_num

        Returns:
            Matching record types
        """
        find_terms = criteria.model_dump(exclude_none=True)
        statement = select(RecordType).options(*_file_links_eager_load())

        for key, value in find_terms.items():
            if key == "name":
                statement = statement.where(cast(RecordType.name, SQLString).like(f"%{value}%"))
            else:
                statement = statement.where(getattr(RecordType, key) == value)

        result = await self.session.execute(statement)
        return result.scalars().all()

    async def ensure_unique_name(self, name: str) -> None:
        """Ensure no record type with this name exists.

        Args:
            name: Record type name to check

        Raises:
            RecordTypeAlreadyExistsError: If a record type with this name already exists
        """
        existing = await self.get_by(name=name)
        if existing:
            raise RecordTypeAlreadyExistsError(name)

    async def validate_parent_type(self, name: str, parent_type_name: str | None) -> None:
        """Validate that setting parent_type_name won't create a cycle.

        Args:
            name: Name of the RecordType being created/updated.
            parent_type_name: Proposed parent type name.

        Raises:
            RecordTypeNotFoundError: If parent type doesn't exist.
            ValidationError: If assignment would create a cycle.
        """
        if parent_type_name is None:
            return

        # Check parent exists
        parent = await self.get_by(name=parent_type_name)
        if not parent:
            raise RecordTypeNotFoundError(parent_type_name)

        # Load all edges and simulate the new one
        all_types = await self.list_all()
        edges: dict[str, str | None] = {rt.name: rt.parent_type_name for rt in all_types}
        edges[name] = parent_type_name

        cycle = detect_cycle(edges)
        if cycle is not None:
            path = " -> ".join(cycle)
            raise ValidationError(f"Setting parent_type_name would create a cycle: {path}")
