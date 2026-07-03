"""Unit tests for :mod:`clarinet.services.recordflow.call_function_registry`.

The registry is what allows the ``/api/admin/workflow/dispatch`` endpoint and
the ``call_registered_callable`` TaskIQ task to recover a live
``CallFunctionAction`` from the node id emitted by the workflow_graph builder.
"""

from __future__ import annotations

import pytest

from clarinet.services.recordflow import call_function_registry
from clarinet.services.recordflow.flow_action import CallFunctionAction


def sample_callable(record, context, client):
    return None


def another_callable():
    return None


@pytest.fixture(autouse=True)
def _reset_registry():
    call_function_registry.reset()
    yield
    call_function_registry.reset()


def test_make_id_format():
    node_id = call_function_registry.make_call_function_id(sample_callable)
    assert node_id == f"call:{sample_callable.__module__}.sample_callable"


def test_register_and_get_round_trip():
    action = CallFunctionAction(function=sample_callable, args=(), extra_kwargs={})
    node_id = call_function_registry.register(action)
    fetched = call_function_registry.get(node_id)
    assert fetched is action


def test_get_unknown_returns_none():
    assert call_function_registry.get("call:nope.missing") is None


def test_register_same_function_overwrites_last_wins():
    a = CallFunctionAction(function=sample_callable, args=(), extra_kwargs={"k": 1})
    b = CallFunctionAction(function=sample_callable, args=(), extra_kwargs={"k": 2})
    id_a = call_function_registry.register(a)
    id_b = call_function_registry.register(b)
    assert id_a == id_b
    assert call_function_registry.get(id_a) is b


def test_register_distinct_functions_yields_distinct_ids():
    a = CallFunctionAction(function=sample_callable, args=(), extra_kwargs={})
    b = CallFunctionAction(function=another_callable, args=(), extra_kwargs={})
    call_function_registry.register(a)
    call_function_registry.register(b)
    ids = call_function_registry.all_ids()
    assert len(ids) == 2
    assert ids == sorted(ids)  # all_ids() returns sorted output


def test_reset_clears_registry():
    action = CallFunctionAction(function=sample_callable, args=(), extra_kwargs={})
    call_function_registry.register(action)
    assert call_function_registry.all_ids()
    call_function_registry.reset()
    assert call_function_registry.all_ids() == []


def test_call_dsl_method_registers():
    """Verify that ``record('x').call(f)`` automatically populates the registry."""
    from clarinet.services.recordflow import RECORD_REGISTRY, record

    RECORD_REGISTRY.clear()
    record("rt-test").on_status("finished").call(sample_callable)
    node_id = call_function_registry.make_call_function_id(sample_callable)
    assert call_function_registry.get(node_id) is not None


def _make_record_read(name: str, record_id: int = 1):
    from datetime import UTC, datetime

    from clarinet.models.base import DicomQueryLevel, RecordStatus
    from clarinet.models.patient import PatientBase
    from clarinet.models.record import RecordRead, RecordTypeBase
    from clarinet.models.study import StudyBase

    type_name = name if len(name) >= 5 else f"{name}-type"
    return RecordRead(
        id=record_id,
        data=None,
        status=RecordStatus.finished,
        record_type_name=type_name,
        patient_id="PAT001",
        study_uid="1.2.3.4.5",
        created_at=datetime.now(UTC),
        changed_at=datetime.now(UTC),
        patient=PatientBase(id="PAT001", name="Test Patient"),
        study=StudyBase(study_uid="1.2.3.4.5", date=datetime.now(UTC).date(), patient_id="PAT001"),
        series=None,
        record_type=RecordTypeBase(name=type_name, level=DicomQueryLevel.STUDY),
    )


@pytest.mark.asyncio
async def test_call_registered_callable_paginates_via_iter_records():
    """The task body builds record_context via iter_records, not first-page find_records."""
    from unittest.mock import AsyncMock, MagicMock

    from clarinet.services.pipeline.context import TaskContext
    from clarinet.services.pipeline.message import PipelineMessage
    from clarinet.services.pipeline.tasks.call_registered_callable import (
        _call_registered_callable_impl,
    )

    captured: dict = {}

    def spy(**kwargs):
        captured.update(kwargs)

    node_id = call_function_registry.register(
        CallFunctionAction(function=spy, args=(), extra_kwargs={})
    )

    trigger = _make_record_read("trigger-type", record_id=1)

    async def _fake_iter(*_args, **_kwargs):
        yield trigger

    client = AsyncMock()
    client.get_record = AsyncMock(return_value=trigger)
    client.find_records = AsyncMock(
        side_effect=AssertionError("must aggregate via iter_records, not find_records")
    )
    client.iter_records = MagicMock(side_effect=_fake_iter)

    ctx = TaskContext(files=MagicMock(), records=MagicMock(), client=client, msg=MagicMock())
    msg = PipelineMessage(
        record_id=1,
        patient_id="PAT001",
        study_uid="1.2.3.4.5",  # required field on PipelineMessage
        payload={"call_function_id": node_id},
    )

    await _call_registered_callable_impl(msg, ctx)

    client.iter_records.assert_called_once_with(patient_id="PAT001")
    client.find_records.assert_not_called()
    assert any(r.id == 1 for records in captured["context"].values() for r in records)
