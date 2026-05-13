"""Tree-filtered record-context builder shared by the engine and the
direct-dispatch task.

The :class:`RecordFlowEngine` builds a per-trigger context dict
``{record_type_name: [RecordRead, ...]}`` filtered to the DICOM-tree slice
(ancestors + subtree of the trigger). The same shape is required by
:class:`CallFunctionAction` callbacks dispatched manually via
``POST /api/admin/workflow/dispatch`` — so the logic lives here instead of as
a private engine method, and both call sites pull from the same source.
"""

from __future__ import annotations

from clarinet.models import DicomQueryLevel, RecordRead


def record_in_tree(
    record: RecordRead,
    trigger_level: DicomQueryLevel | None,
    trigger_study_uid: str | None,
    trigger_series_uid: str | None,
) -> bool:
    """Keep records on the ancestors-plus-subtree slice of the trigger.

    See :class:`RecordFlowEngine._record_in_tree` for the rationale; this is
    the canonical implementation, the engine delegates here.
    """
    if record.record_type is None:
        return False
    record_level = record.record_type.level
    if record_level == DicomQueryLevel.PATIENT:
        return True
    if record_level == DicomQueryLevel.STUDY:
        if trigger_level == DicomQueryLevel.PATIENT:
            return True
        if trigger_study_uid is None:
            return False
        return record.study_uid == trigger_study_uid
    if record_level == DicomQueryLevel.SERIES:
        if trigger_level == DicomQueryLevel.PATIENT:
            return True
        if trigger_level == DicomQueryLevel.STUDY:
            if trigger_study_uid is None:
                return False
            return record.study_uid == trigger_study_uid
        if trigger_series_uid is None:
            return False
        return record.series_uid == trigger_series_uid
    return False


def build_record_context(
    records: list[RecordRead],
    trigger: RecordRead,
) -> dict[str, list[RecordRead]]:
    """Filter and group records by type for the trigger's tree slice."""
    trigger_level = trigger.record_type.level if trigger.record_type else None
    trigger_study_uid = trigger.study_uid
    trigger_series_uid = trigger.series_uid

    context: dict[str, list[RecordRead]] = {}
    for r in records:
        if not (r.record_type and r.record_type.name):
            continue
        if not record_in_tree(r, trigger_level, trigger_study_uid, trigger_series_uid):
            continue
        context.setdefault(r.record_type.name, []).append(r)

    for lst in context.values():
        lst.sort(key=lambda x: x.id or 0)
    return context
