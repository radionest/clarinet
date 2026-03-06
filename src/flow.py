"""Public API for Clarinet flow DSL.

Re-exports RecordFlow primitives and pipeline task decorator
under a single namespace::

    from clarinet.flow import Field as F, record, study, task
"""

from __future__ import annotations

from typing import Any

from src.services.pipeline import pipeline_task
from src.services.recordflow import Field, patient, record, series, study

__all__ = [
    "Field",
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
