"""Pipeline definition API router.

Provides access to pipeline definitions for workers.
No authentication required — workers need unauthenticated access.
"""

from fastapi import APIRouter

from src.api.dependencies import PipelineDefinitionRepositoryDep
from src.models.pipeline_definition import PipelineDefinition, PipelineDefinitionRead

router = APIRouter()


@router.get("/{name}/definition", response_model=PipelineDefinitionRead)
async def get_pipeline_definition(
    name: str,
    repo: PipelineDefinitionRepositoryDep,
) -> PipelineDefinition:
    """Get pipeline definition by name.

    Args:
        name: Pipeline name.
        repo: Pipeline definition repository.

    Returns:
        Pipeline definition with steps.

    Raises:
        EntityNotFoundError: If pipeline not found (→ 404).
    """
    return await repo.get(name)


@router.post("/sync")
async def sync_definitions(
    repo: PipelineDefinitionRepositoryDep,
) -> dict[str, int]:
    """Sync in-memory pipeline definitions to database.

    Re-reads all registered ``Pipeline`` objects and upserts
    their step definitions. Use after modifying pipeline
    definitions without restarting the server.

    Returns:
        Number of synced definitions.
    """
    from src.services.pipeline.chain import _PIPELINE_REGISTRY

    for pipeline in _PIPELINE_REGISTRY.values():
        await repo.upsert(pipeline.name, [s.to_dict() for s in pipeline.steps])

    return {"synced": len(_PIPELINE_REGISTRY)}
