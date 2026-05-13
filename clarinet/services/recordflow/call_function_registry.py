"""Registry of CallFunctionAction instances by stable node id.

The visualization layer (``services/workflow_graph``) emits a graph node per
unique ``CallFunctionAction`` keyed by ``call:{function.__module__}.{function.__name__}``.
The admin dispatch endpoint (``POST /api/admin/workflow/dispatch``) needs to
recover the actual ``Callable`` from such an id — but ``Callable`` objects
can't cross process boundaries via TaskIQ. This registry fills the gap:

* On flow load (in both the API process and each worker), every ``.call(f)``
  in the DSL inserts the action here.
* The dispatch endpoint and the ``call_registered_callable`` task both look
  up by the same id, so the same set of callables is reachable regardless of
  which process the work lands in.

Security: id'ы регистрируются только из реально загруженных flow-файлов, поэтому
endpoint не позволяет вызвать произвольный модуль/функцию по пользовательскому
вводу — только уже выбранные автором flow'ов callable'ы.

Last-wins semantics: two ``.call(f)`` for the same ``(module, name)`` overwrite
the earlier entry. In practice they should be the same callable; if signatures
diverge across flow files, the most recently loaded one is dispatched.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clarinet.services.recordflow.flow_action import CallFunctionAction

_REGISTRY: dict[str, CallFunctionAction] = {}


def make_call_function_id(function: Callable[..., Any]) -> str:
    """Stable node id for a callable, matching ``workflow_graph`` builder output."""
    module = getattr(function, "__module__", None) or "?"
    name = getattr(function, "__name__", repr(function))
    return f"call:{module}.{name}"


def register(action: CallFunctionAction) -> str:
    """Register a CallFunctionAction; returns its node id."""
    node_id = make_call_function_id(action.function)
    _REGISTRY[node_id] = action
    return node_id


def get(node_id: str) -> CallFunctionAction | None:
    return _REGISTRY.get(node_id)


def all_ids() -> list[str]:
    return sorted(_REGISTRY.keys())


def reset() -> None:
    _REGISTRY.clear()
