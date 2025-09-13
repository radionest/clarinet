"""Base repository with common CRUD operations."""

from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlmodel import SQLModel, select

from src.exceptions import NOT_FOUND

ModelT = TypeVar("ModelT", bound=SQLModel)
type FilterValueT = str | int | float


class BaseRepository[ModelT: SQLModel]:
    """Base repository providing common database operations."""

    def __init__(self, session: AsyncSession, model_class: type[ModelT]):
        """Initialize repository with session and model class.

        Args:
            session: Database session
            model_class: SQLModel class this repository operates on
        """
        self.session = session
        self.model_class = model_class

    async def get(self, id: Any) -> ModelT:
        """Get entity by ID or raise NOT_FOUND.

        Args:
            id: Entity ID

        Returns:
            Found entity

        Raises:
            NOT_FOUND: If entity doesn't exist
        """
        entity = await self.session.get(self.model_class, id)
        if not entity:
            raise NOT_FOUND.with_context(f"{self.model_class.__name__} with ID {id} not found")
        return entity

    async def get_optional(self, id: Any) -> ModelT | None:
        """Get entity by ID or return None.

        Args:
            id: Entity ID

        Returns:
            Found entity or None
        """
        return await self.session.get(self.model_class, id)

    async def get_by(self, **filters: FilterValueT) -> ModelT | None:
        """Get single entity by filters.

        Args:
            **filters: Field-value pairs to filter by

        Returns:
            Found entity or None
        """
        statement = select(self.model_class)
        for field, value in filters.items():
            if hasattr(self.model_class, field):
                statement = statement.where(getattr(self.model_class, field) == value)

        result = await self.session.execute(statement)
        return result.scalars().first()

    async def exists(self, **filters: FilterValueT) -> bool:
        """Check if entity exists with given filters.

        Args:
            **filters: Field-value pairs to filter by

        Returns:
            True if entity exists
        """
        statement = select(func.count()).select_from(self.model_class)
        for field, value in filters.items():
            if hasattr(self.model_class, field):
                statement = statement.where(getattr(self.model_class, field) == value)

        result = await self.session.execute(statement)
        count = result.scalar()
        return count is not None and count > 0

    async def get_all(
        self, skip: int = 0, limit: int = 100, **filters: FilterValueT
    ) -> Sequence[ModelT]:
        """List entities with pagination and filters.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            **filters: Field-value pairs to filter by

        Returns:
            List of entities
        """
        statement = select(self.model_class)

        for field, value in filters.items():
            if hasattr(self.model_class, field):
                statement = statement.where(getattr(self.model_class, field) == value)

        statement = statement.offset(skip).limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_all(self, **filters: FilterValueT) -> Sequence[ModelT]:
        """List all entities matching filters.

        Args:
            **filters: Field-value pairs to filter by

        Returns:
            List of all matching entities
        """
        statement = select(self.model_class)

        for field, value in filters.items():
            if hasattr(self.model_class, field):
                statement = statement.where(getattr(self.model_class, field) == value)

        result = await self.session.execute(statement)
        return result.scalars().all()

    async def count(self, **filters: FilterValueT) -> int:
        """Count entities matching filters.

        Args:
            **filters: Field-value pairs to filter by

        Returns:
            Number of matching entities
        """
        statement = select(func.count()).select_from(self.model_class)

        for field, value in filters.items():
            if hasattr(self.model_class, field):
                statement = statement.where(getattr(self.model_class, field) == value)

        result = await self.session.execute(statement)
        return result.scalar() or 0

    async def create(self, entity: ModelT) -> ModelT:
        """Create new entity.

        Args:
            entity: Entity to create

        Returns:
            Created entity with refreshed data
        """
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def create_many(self, entities: list[ModelT]) -> list[ModelT]:
        """Create multiple entities.

        Args:
            entities: List of entities to create

        Returns:
            List of created entities
        """
        self.session.add_all(entities)
        await self.session.commit()
        for entity in entities:
            await self.session.refresh(entity)
        return entities

    async def update(
        self, entity: ModelT, update_data: dict[str, Any], exclude_unset: bool = True
    ) -> ModelT:
        """Update entity with given data.

        Args:
            entity: Entity to update
            update_data: Dictionary with fields to update
            exclude_unset: Whether to exclude unset values

        Returns:
            Updated entity
        """
        for field, value in update_data.items():
            if exclude_unset and value is None:
                continue
            if hasattr(entity, field):
                setattr(entity, field, value)

        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        """Delete entity.

        Args:
            entity: Entity to delete
        """
        await self.session.delete(entity)
        await self.session.commit()

    async def delete_by_id(self, id: Any) -> bool:
        """Delete entity by ID.

        Args:
            id: Entity ID

        Returns:
            True if entity was deleted, False if not found
        """
        entity = await self.get_optional(id)
        if entity:
            await self.delete(entity)
            return True
        return False

    def build_query(self, base_query: Select | None = None) -> Select:
        """Build base query for the model.

        Args:
            base_query: Optional base query to extend

        Returns:
            SQLAlchemy Select statement
        """
        if base_query is None:
            return select(self.model_class)
        return base_query

    async def execute_query(self, query: Select) -> Sequence[ModelT]:
        """Execute a custom query.

        Args:
            query: SQLAlchemy Select statement

        Returns:
            Query results
        """
        result = await self.session.execute(query)
        return result.scalars().all()

    async def refresh(self, entity: ModelT, attribute_names: list[str] | None = None) -> ModelT:
        """Refresh entity from database.

        Args:
            entity: Entity to refresh
            attribute_names: Optional list of attributes to refresh

        Returns:
            Refreshed entity
        """
        await self.session.refresh(entity, attribute_names)
        return entity
