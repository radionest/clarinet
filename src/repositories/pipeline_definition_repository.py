"""Repository for pipeline definition CRUD operations."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.pipeline_definition import PipelineDefinition
from src.repositories.base import BaseRepository


class PipelineDefinitionRepository(BaseRepository[PipelineDefinition]):
    """Repository for managing pipeline definitions in the database."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, PipelineDefinition)

    async def upsert(self, name: str, steps: list[dict[str, str]]) -> PipelineDefinition:
        """Create or update a pipeline definition.

        Args:
            name: Unique pipeline identifier.
            steps: Ordered list of step dicts with ``task_name`` and ``queue``.

        Returns:
            The created or updated PipelineDefinition.
        """
        existing = await self.get_optional(name)
        if existing:
            return await self.update(existing, {"steps": steps}, exclude_unset=False)
        return await self.create(PipelineDefinition(name=name, steps=steps))
