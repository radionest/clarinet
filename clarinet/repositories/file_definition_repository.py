"""Repository for FileDefinition database operations."""

from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from clarinet.models.file_schema import (
    FILE_DEFINITION_FIELDS,
    FileDefinition,
    FileDefinitionRead,
    RecordTypeFileLink,
)
from clarinet.repositories.base import BaseRepository
from clarinet.utils.logger import logger


class FileDefinitionRepository(BaseRepository[FileDefinition]):
    """Repository for FileDefinition model operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, FileDefinition)

    async def get_or_create(self, name: str, **kwargs: object) -> FileDefinition:
        """Get existing FileDefinition by name or create a new one.

        If the definition exists, updates whichever mutable fields (pattern,
        description, multiple, level, grid_conform_to, on_grid_mismatch)
        differ from the provided kwargs — and, since the row is shared by
        every RecordType binding it, logs a WARNING naming the other binders
        when such a change reaches any (``_warn_other_binders``).

        Args:
            name: Globally unique file definition name.
            **kwargs: Fields for FileDefinition (pattern, description, multiple,
                level, grid_conform_to, on_grid_mismatch).

        Returns:
            Existing or newly created FileDefinition.
        """
        stmt = select(FileDefinition).where(FileDefinition.name == name)
        result = await self.session.execute(stmt)
        existing = result.scalars().first()

        if existing is not None:
            changed = [
                field_name
                for field_name in FILE_DEFINITION_FIELDS
                if field_name in kwargs and getattr(existing, field_name) != kwargs[field_name]
            ]
            if changed:
                await self._warn_other_binders(existing, changed, kwargs)
                for field_name in changed:
                    setattr(existing, field_name, kwargs[field_name])
                await self.session.flush()
            return existing

        fd = FileDefinition(name=name, **kwargs)
        self.session.add(fd)
        await self.session.flush()
        return fd

    async def _warn_other_binders(
        self, existing: FileDefinition, changed: list[str], new: Mapping[str, object]
    ) -> None:
        """Leave a trace when a row-level change reaches other RecordTypes.

        The API merge fills only the fields an entry omits, so an explicit
        value through one type rewrites the shared row for every other binder
        with no error (#564 is the structural fix). The binders are queried
        rather than read off ``existing.record_type_links``: on PATCH the
        saving type's own links are already flushed away, so the rows left
        are exactly the *other* binders — and a loaded collection could be
        stale.
        """
        stmt = select(RecordTypeFileLink.record_type_name).where(
            RecordTypeFileLink.file_definition_id == existing.id
        )
        binders = sorted(set((await self.session.execute(stmt)).scalars().all()))
        if not binders:
            return
        diff = ", ".join(f"{f}: {getattr(existing, f)!r} -> {new[f]!r}" for f in changed)
        logger.warning(
            f"FileDefinition '{existing.name}' changed ({diff}); the row is also bound by "
            f"RecordType(s) {binders}, whose declarations change with it"
        )

    async def get_by_names(self, names: Iterable[str]) -> dict[str, FileDefinition]:
        """Fetch the existing definitions among *names* in one query, keyed by name."""
        wanted = list(dict.fromkeys(names))
        if not wanted:
            return {}
        stmt = select(FileDefinition).where(col(FileDefinition.name).in_(wanted))
        result = await self.session.execute(stmt)
        return {fd.name: fd for fd in result.scalars().all()}

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
                defn.name, **{name: getattr(defn, name) for name in FILE_DEFINITION_FIELDS}
            )
            result_map[defn.name] = fd
        return result_map
