"""Repository for FileDefinition database operations."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from clarinet.models.file_schema import FileDefinition, FileDefinitionRead
from clarinet.repositories.base import BaseRepository


class FileDefinitionRepository(BaseRepository[FileDefinition]):
    """Repository for FileDefinition model operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, FileDefinition)

    async def get_or_create(self, name: str, **kwargs: object) -> FileDefinition:
        """Get existing FileDefinition by name or create a new one.

        If the definition exists, updates pattern/description/multiple if they
        differ from the provided kwargs.

        Args:
            name: Globally unique file definition name.
            **kwargs: Fields for FileDefinition (pattern, description, multiple).

        Returns:
            Existing or newly created FileDefinition.
        """
        stmt = select(FileDefinition).where(FileDefinition.name == name)
        result = await self.session.execute(stmt)
        existing = result.scalars().first()

        if existing is not None:
            # Update mutable fields if they changed
            changed = False
            for field_name in ("pattern", "description", "multiple", "level"):
                if field_name in kwargs and getattr(existing, field_name) != kwargs[field_name]:
                    setattr(existing, field_name, kwargs[field_name])
                    changed = True
            if changed:
                await self.session.flush()
            return existing

        fd = FileDefinition(name=name, **kwargs)  # type: ignore[arg-type]
        self.session.add(fd)
        await self.session.flush()
        return fd

    async def bulk_upsert(
        self,
        definitions: Sequence[FileDefinitionRead],
    ) -> dict[str, FileDefinition]:
        """Upsert multiple file definitions, returning a name→instance map.

        Args:
            definitions: File definition DTOs to upsert.

        Returns:
            Dict mapping name to FileDefinition instance.
        """
        result_map: dict[str, FileDefinition] = {}
        for defn in definitions:
            fd = await self.get_or_create(
                defn.name,
                pattern=defn.pattern,
                description=defn.description,
                multiple=defn.multiple,
                level=defn.level,
            )
            result_map[defn.name] = fd
        return result_map
