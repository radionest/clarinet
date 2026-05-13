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
