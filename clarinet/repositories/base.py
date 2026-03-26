"""Base repository with common CRUD operations."""

from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy import func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlmodel import SQLModel, select

from clarinet.exceptions.domain import EntityNotFoundError

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
        """Return entity by ID, or raise EntityNotFoundError."""
        entity = await self.session.get(self.model_class, id)
        if not entity:
            raise EntityNotFoundError(f"{self.model_class.__name__} with ID {id} not found")
        return entity

    async def get_optional(self, id: Any) -> ModelT | None:
        return await self.session.get(self.model_class, id)

    async def get_by(self, **filters: FilterValueT) -> ModelT | None:
        statement = select(self.model_class)
        for field, value in filters.items():
            if hasattr(self.model_class, field):
                statement = statement.where(getattr(self.model_class, field) == value)

        result = await self.session.execute(statement)
        return result.scalars().first()

    async def exists(self, **filters: FilterValueT) -> bool:
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
        statement = select(self.model_class)

        for field, value in filters.items():
            if hasattr(self.model_class, field):
                statement = statement.where(getattr(self.model_class, field) == value)

        statement = statement.offset(skip).limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_all(self, **filters: FilterValueT) -> Sequence[ModelT]:
        statement = select(self.model_class)

        for field, value in filters.items():
            if hasattr(self.model_class, field):
                statement = statement.where(getattr(self.model_class, field) == value)

        result = await self.session.execute(statement)
        return result.scalars().all()

    async def count(self, **filters: FilterValueT) -> int:
        statement = select(func.count()).select_from(self.model_class)

        for field, value in filters.items():
            if hasattr(self.model_class, field):
                statement = statement.where(getattr(self.model_class, field) == value)

        result = await self.session.execute(statement)
        return result.scalar() or 0

    async def create(self, entity: ModelT) -> ModelT:
        """Add entity, flush and refresh. Does not commit — caller controls transaction."""
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def create_many(self, entities: list[ModelT]) -> list[ModelT]:
        self.session.add_all(entities)
        await self.session.flush()
        for entity in entities:
            await self.session.refresh(entity)
        return entities

    async def update(
        self,
        entity: ModelT,
        update_data: dict[str, Any],
        exclude_unset: bool = True,
        options: list[Any] | None = None,
    ) -> ModelT:
        """Update entity with given data.

        Args:
            entity: Entity to update
            update_data: Dictionary with fields to update
            exclude_unset: Whether to exclude unset values
            options: Optional SQLAlchemy loader options (e.g. selectinload).
                When provided, re-fetches via select() instead of session.refresh()
                because refresh() does not support loader options.

        Returns:
            Updated entity
        """
        for field, value in update_data.items():
            if exclude_unset and value is None:
                continue
            if hasattr(entity, field):
                setattr(entity, field, value)

        await self.session.commit()

        if options:
            # session.refresh() doesn't support loader options (selectinload etc.),
            # so re-fetch via select().options() using the entity's primary key.
            # Typed as Any to satisfy mypy — sa_inspect returns InstanceState / Mapper.
            inst_state: Any = sa_inspect(entity)
            identity = inst_state.identity  # tuple of PK values from identity map
            mapper: Any = sa_inspect(self.model_class)
            pk_cols = list(mapper.primary_key)  # list of PK Column objects

            stmt = select(self.model_class).options(*options)
            for col, val in zip(pk_cols, identity):
                stmt = stmt.where(col == val)

            result = await self.session.execute(stmt)
            refreshed = result.scalars().first()
            if refreshed is None:
                raise EntityNotFoundError(f"{self.model_class.__name__} not found after update")
            return refreshed

        await self.session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.commit()

    async def delete_by_id(self, id: Any) -> bool:
        entity = await self.get_optional(id)
        if entity:
            await self.delete(entity)
            return True
        return False

    def build_query(self, base_query: Select | None = None) -> Select:
        if base_query is None:
            return select(self.model_class)
        return base_query

    async def execute_query(self, query: Select) -> Sequence[ModelT]:
        result = await self.session.execute(query)
        return result.scalars().all()

    async def refresh(self, entity: ModelT, attribute_names: list[str] | None = None) -> ModelT:
        await self.session.refresh(entity, attribute_names)
        return entity
