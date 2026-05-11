"""Public API for Clarinet flow DSL.

Re-exports RecordFlow primitives, config primitives, and pipeline task
decorator under a single namespace::

    from clarinet.flow import (
        Field,
        FileDef,
        FileRef,
        RecordDef,
        record,
        study,
        task,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast, overload

from clarinet.config.primitives import FileDef, FileRef, RecordDef
from clarinet.services.pipeline import pipeline_task
from clarinet.services.recordflow import Field, file, patient, record, series, study

__all__ = [
    "Field",
    "FileDef",
    "FileRef",
    "RecordDef",
    "file",
    "patient",
    "record",
    "series",
    "study",
    "task",
]


@overload
def task[F: Callable[..., Any]](func: F, /) -> F: ...
@overload
def task[F: Callable[..., Any]](func: None = None, /, **kwargs: Any) -> Callable[[F], F]: ...
def task(
    func: Callable[..., Any] | None = None,
    /,
    **kwargs: Any,
) -> Callable[..., Any]:
    """Pipeline task decorator supporting both bare and parameterised forms.

    Usage::

        @task
        def my_task(msg, ctx): ...


        from clarinet.settings import settings


        @task(queue=settings.gpu_queue_name)
        async def gpu_task(msg, ctx): ...
    """
    if func is not None:
        return cast("Callable[..., Any]", pipeline_task(**kwargs)(func))
    return pipeline_task(**kwargs)
