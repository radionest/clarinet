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
from typing import Annotated, Literal

from cachetools import TTLCache
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from clarinet.api.dependencies import AdminUserDep, RecordRepositoryDep
from clarinet.exceptions.domain import (
    WorkflowDigestAlreadyUsedError,
    WorkflowPlanDigestMismatchError,
)
from clarinet.exceptions.http import NOT_FOUND, SERVICE_UNAVAILABLE, UNPROCESSABLE_ENTITY
from clarinet.models import RecordRead
from clarinet.models.base import RecordStatus
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.pipeline import get_all_pipelines
from clarinet.services.recordflow import ActionPreview
from clarinet.services.recordflow.engine import RecordFlowEngine
from clarinet.services.workflow_graph import (
    ParentRecordAuditProvider,
    WorkflowGraph,
    apply_layout,
    build_graph,
    make_record_type_id,
    subgraph_around_record_type,
)
from clarinet.utils.logger import logger

# /fire idempotency: each fired plan digest is cached for this many seconds
# so retries (network blips, double-clicks) are rejected with 409 rather
# than re-creating records / re-dispatching pipelines. After the TTL the
# admin is free to plan again from scratch.
_USED_DIGEST_TTL_SECONDS = 300
_USED_DIGEST_CACHE_MAX = 256

# Upper bound on direct children loaded for the instance-mode audit
# provider. Admin UIs aren't expected to need more than this; if the
# limit is hit the provider just marks fewer edges as fired.
_INSTANCE_CHILDREN_LIMIT = 1000

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
    status_override: RecordStatus | None = Field(
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


def _get_used_digest_cache(request: Request) -> TTLCache[str, bool]:
    """Return (and lazily create) the per-app TTL cache of consumed digests."""
    cache: TTLCache[str, bool] | None = getattr(request.app.state, "workflow_used_digests", None)
    if cache is None:
        cache = TTLCache(maxsize=_USED_DIGEST_CACHE_MAX, ttl=_USED_DIGEST_TTL_SECONDS)
        request.app.state.workflow_used_digests = cache
    return cache


def _compute_digest(plan: list[ActionPreview]) -> str:
    """Hash a plan for race detection between /dry-run and /fire.

    The digest is stable only if the planner's output is itself stable —
    discovery order of ``*_flow.py`` files matters. ``flow_loader.find_flow_files``
    sorts its results so replicas converge on the same digest.

    No ``default=`` fallback: ``model_dump(mode="json")`` already produces a
    JSON-compatible dict, so any non-JSON value reaching :func:`json.dumps`
    is a programming error — we surface it loudly via ``TypeError`` rather
    than silently stringify it into an unstable digest.
    """
    payload = json.dumps(
        [p.model_dump(mode="json") for p in plan],
        sort_keys=True,
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


def _status_override_value(body: DryRunRequest) -> str | None:
    """Return the string value of ``status_override`` (or ``None`` if absent)."""
    return body.status_override.value if body.status_override is not None else None


async def _run_plan(
    engine: RecordFlowEngine,
    record_read: RecordRead,
    body: DryRunRequest,
) -> list[ActionPreview]:
    match body.trigger_kind:
        case TriggerKindRequest.status:
            return await engine.plan_record_status_change(
                record_read, status_override=_status_override_value(body)
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
            override = _status_override_value(body)
            target = (
                record_read.model_copy(update={"status": override})
                if override is not None
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
    scope: Annotated[
        Literal["schema", "instance"],
        Query(
            description=(
                "schema (default): project-wide graph. "
                "instance: subgraph around record_id's record_type (requires record_id)."
            ),
        ),
    ] = "schema",
) -> WorkflowGraph:
    """Build and return the workflow graph.

    Without ``record_id`` the response is the project-wide schema graph.
    With ``record_id`` (and default ``scope=schema``) the graph carries
    firing-history annotations on edges that ``parent_record_id`` lets us
    reconstruct (today: ``CreateRecord`` edges only).

    With ``scope=instance`` (requires ``record_id``) the graph is restricted
    to a subgraph centered on the record's record_type: parents (types that
    can create it) + children (types it can create), with all intermediate
    pipeline / call / entity / file nodes between them preserved. Firings
    are still annotated when ``record_id`` is set.
    """
    engine = _require_engine(request)
    expanded_pipelines = _parse_expanded(expanded)

    audit_provider = None
    record_read: RecordRead | None = None
    if record_id is not None:
        record_read = await _load_record_read(repo, record_id)
        children = await repo.find_by_criteria(
            RecordSearchCriteria(parent_record_id=record_id),
            limit=_INSTANCE_CHILDREN_LIMIT,
        )
        candidates = [RecordRead.model_validate(r) for r in children]
        audit_provider = ParentRecordAuditProvider(record_read, candidates)

    if scope == "instance" and record_read is None:
        raise UNPROCESSABLE_ENTITY.with_context("scope=instance requires record_id")

    graph = build_graph(
        engine=engine,
        pipelines=get_all_pipelines(),
        audit_provider=audit_provider,
        expanded_pipelines=expanded_pipelines,
    )
    if scope == "instance":
        assert record_read is not None  # narrow for mypy; guarded above
        center_id = make_record_type_id(record_read.record_type_name)
        graph = subgraph_around_record_type(graph, center_id=center_id)
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

    The router is intentionally NOT idempotent at the engine layer
    (CreateRecord, PipelineAction etc. have no built-in dedup). Instead a
    per-app TTL cache of consumed digests blocks replays for ~5 minutes —
    enough to absorb double-clicks and HTTP retries.

    The digest is **reserved before** any DB / engine I/O so that two
    concurrent /fire calls with the same digest cannot both pass the
    "is in cache?" check. On digest mismatch or any exception during
    execution the reservation is released; only a successful execution
    keeps the digest in the cache.

    **Multi-worker caveat**: the cache lives in the per-process
    ``app.state``. With multiple uvicorn/gunicorn workers, replays
    routed to different workers (within the 5-min TTL) will both be
    accepted. Run a single admin worker, or migrate the cache to Redis
    if true multi-worker safety is required.
    """
    engine = _require_engine(request)
    used_digests = _get_used_digest_cache(request)

    if body.plan_digest in used_digests:
        raise WorkflowDigestAlreadyUsedError(body.plan_digest)
    # Reserve before any await — closes the read-then-write race between
    # concurrent /fire calls with the same digest.
    used_digests[body.plan_digest] = True

    success = False
    try:
        record_read = await _load_record_read(repo, body.record_id)
        plan = await _run_plan(engine, record_read, body)
        actual_digest = _compute_digest(plan)
        if actual_digest != body.plan_digest:
            raise WorkflowPlanDigestMismatchError(
                expected_digest=body.plan_digest, current_digest=actual_digest
            )

        logger.info(
            f"Admin {admin.id} firing workflow trigger "
            f"record_id={body.record_id} kind={body.trigger_kind.value} "
            f"status_override={_status_override_value(body)} actions={len(plan)}"
        )
        await _execute_trigger(engine, record_read, body)
        success = True
    finally:
        # Drop the reservation on mismatch / error so the caller can dry-run
        # again with a fresh digest; keep it on success to block replays.
        if not success:
            used_digests.pop(body.plan_digest, None)

    return FireResponse(executed_actions=plan)
