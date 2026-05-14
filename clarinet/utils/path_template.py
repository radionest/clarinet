"""Pure validation primitives for the disk path template setting.

Kept dependency-free (stdlib only) so it can be imported by
``clarinet.settings.Settings`` validators without triggering the rest of
the package graph. The richer rendering helpers — including DB-aware
context building — live in ``clarinet.services.dicom.anon_path``.
"""

from pathlib import PurePosixPath

SUPPORTED_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "anon_patient_id",
        "anon_study_uid",
        "anon_series_uid",
        "patient_id",
        "patient_auto_id",
        "anon_id_prefix",
        "study_uid",
        "series_uid",
        "study_date",
        "study_modalities",
        "series_modality",
        "series_num",
    }
)


class StrictDict(dict[str, str]):
    """Dict subclass that raises ``KeyError(key)`` on missing keys.

    ``str.format_map`` swallows ``KeyError`` without context — using this
    wrapper produces error messages that name the offending placeholder.
    """

    def __missing__(self, key: str) -> str:
        raise KeyError(key)


def validate_template(template: str) -> str:
    """Validate a disk path template; return the normalized template.

    Used by both ``settings.disk_path_template`` validator and the
    migration CLI (which validates both ``--from`` and ``--to`` values).
    """
    if not template.strip():
        raise ValueError("disk_path_template must be non-empty")
    if template.startswith(("/", "\\")):
        raise ValueError("disk_path_template must be relative (no leading slash)")
    parts = template.split("/")
    if len(parts) != 3 or any(not p.strip() for p in parts):
        raise ValueError(
            f"disk_path_template must have exactly 3 '/'-separated non-empty segments, "
            f"got {len(parts)}: {template!r}"
        )
    if ".." in PurePosixPath(template).parts:
        raise ValueError("disk_path_template must not contain '..'")
    dummy = dict.fromkeys(SUPPORTED_PLACEHOLDERS, "x")
    try:
        template.format_map(StrictDict(dummy))
    except KeyError as exc:
        raise ValueError(
            f"disk_path_template references unknown placeholder {exc.args[0]!r}; "
            f"supported: {sorted(SUPPORTED_PLACEHOLDERS)}"
        ) from exc
    return template
