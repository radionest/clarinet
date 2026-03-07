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

from typing import Any

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


def task(func: Any = None, /, **kwargs: Any) -> Any:
    """Pipeline task decorator supporting both bare and parameterised forms.

    Usage::

        @task
        def my_task(msg, ctx): ...


        @task(queue="clarinet.gpu")
        async def gpu_task(msg, ctx): ...
    """
    if func is not None:
        return pipeline_task(**kwargs)(func)
    return pipeline_task(**kwargs)
