"""Pluggable source of "what already fired" evidence for the workflow graph.

Today there is one provider — :class:`ParentRecordAuditProvider` — which
recovers ``CreateRecord`` firings from ``parent_record_id`` links on existing
records. When a real audit table lands later, add a sibling provider that
reads it and merge them via :class:`CompositeAuditProvider`. The graph
builder treats them all the same.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .models import FiringRecord, FiringSource

if TYPE_CHECKING:
    from clarinet.models import RecordRead


class WorkflowAuditProvider(Protocol):
    """Returns historical firings keyed by edge identity.

    The key is a tuple ``(from_node_id, to_node_id, edge_kind_value)`` — the
    minimal triple uniquely identifying an edge slot in :class:`WorkflowGraph`.
    Two flows that produce the same edge (same source, same target, same
    action kind) share one slot and accumulate firings together.
    """

    def get_firings(self) -> dict[tuple[str, str, str], list[FiringRecord]]: ...


class ParentRecordAuditProvider:
    """Recover ``CreateRecord`` firings from ``parent_record_id`` links.

    Instance-mode usage: pass the trigger record + records the API returned
    for the same patient. Direct children (records whose ``parent_record_id``
    equals the trigger's id) become firings on the edge from the trigger's
    record-type to the child's record-type.

    This does NOT cover Invalidate / Update / Pipeline firings — those don't
    leave a ``parent_record_id`` trail. They will arrive when an audit table
    is added.
    """

    def __init__(
        self,
        trigger_record: RecordRead,
        candidate_records: list[RecordRead],
    ):
        self._trigger = trigger_record
        self._candidates = candidate_records

    def get_firings(self) -> dict[tuple[str, str, str], list[FiringRecord]]:
        if self._trigger.record_type is None or self._trigger.id is None:
            return {}

        from .models import EdgeKind, make_record_type_id

        trigger_node = make_record_type_id(self._trigger.record_type.name)
        result: dict[tuple[str, str, str], list[FiringRecord]] = {}

        for candidate in self._candidates:
            if candidate.parent_record_id != self._trigger.id:
                continue
            if candidate.record_type is None:
                continue
            child_node = make_record_type_id(candidate.record_type.name)
            key = (trigger_node, child_node, EdgeKind.CREATE_RECORD.value)
            firing = FiringRecord(
                fired_at=candidate.created_at,
                source=FiringSource.PARENT_RECORD_ID,
                metadata={
                    "child_record_id": candidate.id,
                    "child_record_type": candidate.record_type.name,
                },
            )
            result.setdefault(key, []).append(firing)

        return result


class CompositeAuditProvider:
    """Merge firings from multiple providers into one map (later use)."""

    def __init__(self, providers: list[WorkflowAuditProvider]):
        self._providers = providers

    def get_firings(self) -> dict[tuple[str, str, str], list[FiringRecord]]:
        merged: dict[tuple[str, str, str], list[FiringRecord]] = {}
        for p in self._providers:
            for key, firings in p.get_firings().items():
                merged.setdefault(key, []).extend(firings)
        return merged
