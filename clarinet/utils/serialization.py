"""Project-standard JSON serialization.

Single source of orjson options for the HTTP client, logger, and pipeline
DLQ. Change here if encoder options need tuning (e.g. OPT_NAIVE_UTC).
"""

import orjson


def json_dumps_bytes(obj: object) -> bytes:
    """Serialize *obj* to JSON bytes via orjson.

    UUID/datetime/date are handled natively by orjson; ``default=str`` is
    a safety net for Path/Decimal/Enum and unknown user types.
    """
    return orjson.dumps(obj, default=str)
