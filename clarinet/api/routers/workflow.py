"""Admin-only workflow visualization & dry-run/fire endpoints.

Mounted at ``/api/admin/workflow``. All routes require admin role
(``AdminUserDep``). The router is a thin shell over
:mod:`clarinet.services.workflow_graph` (graph builder + layout) and
``RecordFlowEngine.plan_*`` / ``handle_*`` (dry-run vs execute).
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from clarinet.api.dependencies import AdminUserDep, RecordRepositoryDep
from clarinet.exceptions.http import NOT_FOUND, SERVICE_UNAVAILABLE
from clarinet.models import RecordRead
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.pipeline import get_all_pipelines
from clarinet.services.recordflow import ActionPreview
from clarinet.services.recordflow.engine import RecordFlowEngine
from clarinet.services.workflow_graph import (
    ParentRecordAuditProvider,
    WorkflowGraph,
    apply_layout,
    build_graph,
)
from clarinet.utils.logger import logger

router = APIRouter(
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Forbidden"},
        404: {"description": "Not found"},
        409: {"description": "Plan digest mismatch"},
        422: {"description": "Validation error"},
        503: {"description": "RecordFlow disabled"},
    },
)


class TriggerKindRequest(str, Enum):
    """Which engine ``handle_*`` to invoke."""

    status = "status"
    data_update = "data_update"
    file_change = "file_change"


class DryRunRequest(BaseModel):
    record_id: int = Field(ge=1, le=2147483647)
    trigger_kind: TriggerKindRequest
    status_override: str | None = Field(
        default=None,
        description=(
            "When trigger_kind='status' — pretend the record is in this status "
            "(without mutating it). Ignored for data_update / file_change."
        ),
    )


class DryRunResponse(BaseModel):
    plan: list[ActionPreview]
    digest: str = Field(description="Stable hash of `plan` for replay protection in /fire.")


class FireRequest(DryRunRequest):
    plan_digest: str = Field(description="Digest from a prior /dry-run for the same trigger.")


class FireResponse(BaseModel):
    executed_actions: list[ActionPreview]


def _require_engine(request: Request) -> RecordFlowEngine:
    engine: RecordFlowEngine | None = getattr(request.app.state, "recordflow_engine", None)
    if engine is None:
        raise SERVICE_UNAVAILABLE.with_context(
            "RecordFlow is disabled (set recordflow_enabled=True to enable)."
        )
    return engine


def _compute_digest(plan: list[ActionPreview]) -> str:
    """Hash a plan for race detection between /dry-run and /fire."""
    payload = json.dumps(
        [p.model_dump(mode="json") for p in plan],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _parse_expanded(value: str | None) -> set[str]:
    if not value:
        return set()
    return {p for p in (s.strip() for s in value.split(",")) if p}


async def _load_record_read(repo: RecordRepositoryDep, record_id: int) -> RecordRead:
    record = await repo.get_with_relations(record_id)
    if record is None:
        raise NOT_FOUND.with_context(f"Record {record_id} not found")
    return RecordRead.model_validate(record)


async def _run_plan(
    engine: RecordFlowEngine,
    record_read: RecordRead,
    body: DryRunRequest,
) -> list[ActionPreview]:
    match body.trigger_kind:
        case TriggerKindRequest.status:
            return await engine.plan_record_status_change(
                record_read, status_override=body.status_override
            )
        case TriggerKindRequest.data_update:
            return await engine.plan_record_data_update(record_read)
        case TriggerKindRequest.file_change:
            return await engine.plan_record_file_change(record_read)


async def _execute_trigger(
    engine: RecordFlowEngine,
    record_read: RecordRead,
    body: FireRequest,
) -> None:
    match body.trigger_kind:
        case TriggerKindRequest.status:
            target = (
                record_read.model_copy(update={"status": body.status_override})
                if body.status_override is not None
                else record_read
            )
            await engine.handle_record_status_change(target)
        case TriggerKindRequest.data_update:
            await engine.handle_record_data_update(record_read)
        case TriggerKindRequest.file_change:
            await engine.handle_record_file_change(record_read)


@router.get("/graph", response_model=WorkflowGraph)
async def get_graph(
    request: Request,
    _admin: AdminUserDep,
    repo: RecordRepositoryDep,
    record_id: Annotated[int | None, Query(ge=1, le=2147483647)] = None,
    expanded: Annotated[
        str | None, Query(description="Comma-separated list of pipeline names to inline.")
    ] = None,
) -> WorkflowGraph:
    """Build and return the workflow graph.

    Without ``record_id`` the response is the project-wide schema graph.
    With ``record_id`` the graph carries firing-history annotations on edges
    that ``parent_record_id`` lets us reconstruct (today: ``CreateRecord``
    edges only).
    """
    engine = _require_engine(request)
    expanded_pipelines = _parse_expanded(expanded)

    audit_provider = None
    if record_id is not None:
        record_read = await _load_record_read(repo, record_id)
        children = await repo.find_by_criteria(RecordSearchCriteria(parent_record_id=record_id))
        candidates = [RecordRead.model_validate(r) for r in children]
        audit_provider = ParentRecordAuditProvider(record_read, candidates)

    graph = build_graph(
        engine=engine,
        pipelines=get_all_pipelines(),
        audit_provider=audit_provider,
        expanded_pipelines=expanded_pipelines,
    )
    apply_layout(graph)
    return graph


@router.post("/dry-run", response_model=DryRunResponse)
async def dry_run(
    request: Request,
    _admin: AdminUserDep,
    repo: RecordRepositoryDep,
    body: DryRunRequest,
) -> DryRunResponse:
    """Plan what would happen if a trigger fired — without executing.

    Returns the list of actions the engine would dispatch plus a stable
    ``digest`` the caller passes back to :func:`fire` to detect drift.
    """
    engine = _require_engine(request)
    record_read = await _load_record_read(repo, body.record_id)
    plan = await _run_plan(engine, record_read, body)
    return DryRunResponse(plan=plan, digest=_compute_digest(plan))


@router.post("/fire", response_model=FireResponse)
async def fire(
    request: Request,
    admin: AdminUserDep,
    repo: RecordRepositoryDep,
    body: FireRequest,
) -> FireResponse:
    """Execute a previously-planned trigger after digest verification.

    Replays the plan, compares its digest with the client-supplied one, and
    runs the real :meth:`RecordFlowEngine.handle_*` only on match. The
    returned ``executed_actions`` mirrors the validated plan so the UI can
    display exactly what was confirmed by the admin.
    """
    engine = _require_engine(request)
    record_read = await _load_record_read(repo, body.record_id)

    plan = await _run_plan(engine, record_read, body)
    actual_digest = _compute_digest(plan)
    if actual_digest != body.plan_digest:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WORKFLOW_PLAN_CHANGED",
                "message": "Plan changed since dry-run; re-run dry-run before firing.",
                "expected_digest": body.plan_digest,
                "current_digest": actual_digest,
            },
        )

    logger.info(
        f"Admin {admin.id} firing workflow trigger "
        f"record_id={body.record_id} kind={body.trigger_kind.value} "
        f"status_override={body.status_override} actions={len(plan)}"
    )
    await _execute_trigger(engine, record_read, body)
    return FireResponse(executed_actions=plan)
