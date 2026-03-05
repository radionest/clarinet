"""Repository for FileDefinition database operations."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.models.file_schema import FileDefinition
from src.repositories.base import BaseRepository


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
            for field_name in ("pattern", "description", "multiple"):
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

    async def bulk_upsert(self, definitions: list[dict[str, Any]]) -> dict[str, FileDefinition]:
        """Upsert multiple file definitions, returning a name→instance map.

        Args:
            definitions: List of dicts with keys: name, pattern, description, multiple.

        Returns:
            Dict mapping name to FileDefinition instance.
        """
        result_map: dict[str, FileDefinition] = {}
        for defn in definitions:
            name = str(defn["name"])
            kwargs = {k: v for k, v in defn.items() if k != "name"}
            fd = await self.get_or_create(name, **kwargs)
            result_map[name] = fd
        return result_map
