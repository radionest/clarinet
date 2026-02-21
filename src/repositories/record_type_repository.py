"""Repository for RecordType-specific database operations."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import String as SQLString
from sqlalchemy import cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.exceptions.domain import RecordTypeAlreadyExistsError, RecordTypeNotFoundError
from src.models.record import RecordType, RecordTypeFind
from src.repositories.base import BaseRepository


class RecordTypeRepository(BaseRepository[RecordType]):
    """Repository for RecordType model operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, RecordType)

    async def get(self, id: Any) -> RecordType:
        """Get record type by ID or raise RecordTypeNotFoundError.

        Args:
            id: Record type ID

        Returns:
            Found record type

        Raises:
            RecordTypeNotFoundError: If record type doesn't exist
        """
        entity = await self.session.get(self.model_class, id)
        if not entity:
            raise RecordTypeNotFoundError(id)
        return entity

    async def find(self, criteria: RecordTypeFind) -> Sequence[RecordType]:
        """Find record types by criteria.

        Args:
            criteria: Search criteria with optional name, constraint_role, constraint_user_num

        Returns:
            Matching record types
        """
        find_terms = criteria.model_dump(exclude_none=True)
        statement = select(RecordType)

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
