"""Schema-aware scrubbing of free text from record-data JSON.

Used by the DB scrubber (``clarinet anon scrub-db``) to strip free-text PII
from ``record.data`` (and the record snapshots embedded in
``record_event.old_value`` / ``new_value``) while preserving the structural
fields the workflow needs to replay on a test stand.

Policy — a string is **kept** only when the schema marks it as structured:

* an ``enum`` / ``const`` choice, or
* an option-sourced identifier (``x-options``, e.g. a DICOM series UID).

Every other string is free text and is replaced with :data:`REDACTION`.
Numbers, booleans and ``null`` are always preserved. Fields absent from the
schema are treated as free text too (unknown ⇒ scrub), so a stale or partial
schema fails safe rather than leaking. The known DICOM/imaging schemas express
the fields a downstream stage needs (enums, integers, ``x-options`` UIDs)
structurally, so this preserves exactly what replay requires.

Pure functions, no I/O — unit-tested in isolation.
"""

from typing import Any

from clarinet.types import RecordData, RecordSchema

# Free-text replacement. JSON-Schema validation is not re-run after scrubbing,
# so an empty string is safe even for fields that declare a minLength.
REDACTION = ""

# JSON-Schema keywords whose sub-schemas may (re)define properties of the same
# object — merged so a field constrained only inside a conditional branch is
# still recognised as structured.
_BRANCH_KEYS = ("then", "else", "if")
_COMBINATOR_KEYS = ("allOf", "anyOf", "oneOf")


def scrub_record_data(data: RecordData | None, schema: RecordSchema | None) -> RecordData | None:
    """Return a copy of ``data`` with free-text values redacted per ``schema``.

    ``None`` in, ``None`` out. ``schema`` may be ``None`` (no schema for the
    record type) — then every string leaf is treated as free text. The input
    is never mutated.
    """
    if data is None:
        return None
    return _scrub_object(data, schema or {})


def _is_structured(schema: dict[str, Any] | None) -> bool:
    """True if ``schema`` marks its value as a structured (preserve) string."""
    if not schema:
        return False
    return "enum" in schema or "const" in schema or "x-options" in schema


def _scrub_value(value: Any, schema: dict[str, Any] | None) -> Any:
    schema = schema or {}
    if "enum" in schema or "const" in schema:
        # A fixed choice — preserve regardless of the JSON type.
        return value
    if isinstance(value, dict):
        return _scrub_object(value, schema)
    if isinstance(value, list):
        return _scrub_array(value, schema)
    if isinstance(value, str):
        return value if "x-options" in schema else REDACTION
    # int / float / bool / None — structural, preserved.
    return value


def _scrub_object(obj: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    props = _collect_property_schemas(schema)
    return {key: _scrub_value(val, props.get(key)) for key, val in obj.items()}


def _scrub_array(arr: list[Any], schema: dict[str, Any]) -> list[Any]:
    items = schema.get("items")
    item_schema = items if isinstance(items, dict) else None
    return [_scrub_value(val, item_schema) for val in arr]


def _collect_property_schemas(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Merge ``properties`` from an object schema and its conditional branches.

    On a duplicate key the more-structured schema wins, so a field declared
    free text at the top level but constrained (enum/const/x-options) inside a
    ``then`` / ``oneOf`` branch is still preserved.
    """
    result: dict[str, dict[str, Any]] = {}

    def merge(props: Any) -> None:
        if not isinstance(props, dict):
            return
        for key, sub in props.items():
            sub = sub if isinstance(sub, dict) else {}
            if key not in result or not _is_structured(result[key]):
                result[key] = sub

    merge(schema.get("properties"))
    for branch in _BRANCH_KEYS:
        sub = schema.get(branch)
        if isinstance(sub, dict):
            merge(sub.get("properties"))
    for combinator in _COMBINATOR_KEYS:
        for item in schema.get(combinator) or []:
            if isinstance(item, dict):
                for key, sub in _collect_property_schemas(item).items():
                    if key not in result or not _is_structured(result[key]):
                        result[key] = sub
    return result
