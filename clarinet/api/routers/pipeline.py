"""Pipeline definition and task run audit API router.

Definition endpoints require no authentication — workers need
unauthenticated access. Run audit endpoints are admin-only for both
read and write: AuditMiddleware authenticates with ``X-Internal-Token``,
which resolves to the admin user, and regular users must not be able
to forge or overwrite audit rows.
"""

from datetime import datetime

from fastapi import APIRouter

from clarinet.api.dependencies import (
    AdminUserDep,
    PaginationDep,
    PipelineDefinitionRepositoryDep,
    PipelineTaskRunRepositoryDep,
)
from clarinet.exceptions import NOT_FOUND
from clarinet.models.pipeline_definition import PipelineDefinition, PipelineDefinitionRead
from clarinet.models.pipeline_task_run import (
    PipelineRunStatus,
    PipelineTaskRun,
    PipelineTaskRunCreate,
    PipelineTaskRunFind,
    PipelineTaskRunRead,
    PipelineTaskRunUpdate,
)

router = APIRouter(
    responses={
        400: {"description": "Bad request (malformed body)"},
        401: {"description": "Not authenticated"},
        403: {"description": "Forbidden"},
        404: {"description": "Not found"},
        422: {"description": "Validation error"},
    },
)


@router.post("/runs", response_model=PipelineTaskRunRead, status_code=201)
async def create_pipeline_run(
    body: PipelineTaskRunCreate,
    _user: AdminUserDep,
    repo: PipelineTaskRunRepositoryDep,
) -> PipelineTaskRun:
    """Create an audit row for a started pipeline task.

    Called by ``AuditMiddleware.pre_execute`` with the worker service token.
    Idempotent on duplicate ``id`` — re-delivered messages keep the original row.
    """
    return await repo.upsert_start(body)


@router.get("/runs", response_model=list[PipelineTaskRunRead])
async def list_pipeline_runs(
    _user: AdminUserDep,
    repo: PipelineTaskRunRepositoryDep,
    pagination: PaginationDep,
    status: PipelineRunStatus | None = None,
    task_name: str | None = None,
    record_id: int | None = None,
    since: datetime | None = None,
) -> list[PipelineTaskRun]:
    """List pipeline task runs with optional filters, newest first (admin only)."""
    criteria = PipelineTaskRunFind(
        status=status,
        task_name=task_name,
        record_id=record_id,
        since=since,
        skip=pagination.skip,
        limit=pagination.limit,
    )
    return list(await repo.find(criteria))


@router.get("/runs/{task_id}", response_model=PipelineTaskRunRead)
async def get_pipeline_run(
    task_id: str,
    _user: AdminUserDep,
    repo: PipelineTaskRunRepositoryDep,
) -> PipelineTaskRun:
    """Get a single pipeline task run by task id (admin only).

    Raises:
        EntityNotFoundError: If the run is unknown (→ 404).
    """
    return await repo.get(task_id)


@router.patch("/runs/{task_id}", response_model=PipelineTaskRunRead)
async def finish_pipeline_run(
    task_id: str,
    body: PipelineTaskRunUpdate,
    _user: AdminUserDep,
    repo: PipelineTaskRunRepositoryDep,
) -> PipelineTaskRun:
    """Record terminal status for a pipeline task run.

    Called by ``AuditMiddleware.post_execute`` with the worker service token.
    """
    run = await repo.finish(task_id, body)
    if run is None:
        raise NOT_FOUND.with_context(f"PipelineTaskRun '{task_id}' not found")
    return run


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
    from clarinet.services.pipeline import persist_definitions

    return {"synced": await persist_definitions(repo)}
