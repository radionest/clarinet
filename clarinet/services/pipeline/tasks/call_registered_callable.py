"""Generic TaskIQ task that dispatches a :class:`CallFunctionAction` by id.

Used by ``POST /api/admin/workflow/dispatch`` to enqueue a manual run of a
``.call(func)`` action without firing the surrounding flow. The endpoint
puts the registered node id into ``msg.payload['call_function_id']``; this
task resolves it via
:mod:`clarinet.services.recordflow.call_function_registry` (populated when
flow modules are imported in this process) and invokes the bound callable
with the same kwargs shape the engine's ``_call_function`` uses for
record-triggered flows.

Caveats:

* The worker process MUST load the same flow files as the API process (see
  ``settings.recordflow_paths``); otherwise the registry won't contain the
  requested id and the task raises ``ValueError`` → routes to DLQ.
* Manual dispatch bypasses conditions / match / case — admin is responsible
  for verifying the function makes sense for the chosen record.
"""

from __future__ import annotations

import asyncio
from typing import Any

from clarinet.services.pipeline.context import TaskContext
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.task import pipeline_task
from clarinet.utils.logger import logger


@pipeline_task()
async def call_registered_callable(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Dispatch a registered :class:`CallFunctionAction` by its node id."""
    from clarinet.services.recordflow import call_function_registry
    from clarinet.services.recordflow.context_builder import build_record_context

    node_id = msg.payload.get("call_function_id")
    if not node_id:
        raise ValueError("call_registered_callable: msg.payload['call_function_id'] is required")

    action = call_function_registry.get(node_id)
    if action is None:
        raise ValueError(
            f"call_registered_callable: unknown call_function node '{node_id}' — "
            f"is this worker loading the same flow files as the API process?"
        )

    if msg.record_id is None:
        raise ValueError(
            "call_registered_callable: msg.record_id is required for record-scoped dispatch"
        )

    record = await ctx.client.get_record(msg.record_id)

    record_context: dict[str, list[Any]]
    if record.patient_id is None:
        logger.warning(
            f"Record {record.id} has no patient_id; dispatching {node_id} with empty record_context"
        )
        record_context = {}
    else:
        records = await ctx.client.find_records(patient_id=record.patient_id, limit=1000)
        record_context = build_record_context(records, record)

    kwargs: dict[str, Any] = {
        "record": record,
        "context": record_context,
        "client": ctx.client,
    }
    kwargs |= action.extra_kwargs

    logger.info(f"call_registered_callable dispatching {node_id} for record_id={record.id}")
    result = action.function(*action.args, **kwargs)
    if asyncio.iscoroutine(result):
        await result
