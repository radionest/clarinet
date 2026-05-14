"""Pure validation primitives for the disk path template setting.

Kept dependency-free (stdlib only) so it can be imported by
``clarinet.settings.Settings`` validators without triggering the rest of
the package graph. The richer rendering helpers — including DB-aware
context building — live in ``clarinet.services.common.storage_paths``.

A template has exactly three ``/``-separated non-empty segments mapping
to the PATIENT / STUDY / SERIES levels of the DICOM hierarchy. Each
segment may reference any subset of ``SUPPORTED_PLACEHOLDERS`` below.
Placeholders are interpolated via ``str.format_map`` against a context
built by ``clarinet.services.common.storage_paths.build_context``. Missing
fields resolve to ``"unknown"`` rather than raising, so reader-side
lookups stay non-fatal on incomplete data.

Supported placeholders
----------------------

==================  =========================================  =====================  =============
Placeholder         Source                                     Format                 Fallback
==================  =========================================  =====================  =============
anon_patient_id     ``patient.anon_id`` or per-study hash       string                 ``"unknown"``
anon_study_uid      ``study.anon_uid`` → ``study.study_uid``    DICOM UID              ``"unknown"``
anon_series_uid     ``series.anon_uid`` → ``series.series_uid`` DICOM UID              ``"unknown"``
patient_id          DICOM PatientID (``patient.id``)            string                 ``"unknown"``
patient_auto_id     ``patient.auto_id`` (monotonic counter)     integer                ``"unknown"``
anon_id_prefix      ``settings.anon_id_prefix``                 string                 ``"anon"``
study_uid           ``study.study_uid``                         DICOM UID              ``"unknown"``
series_uid          ``series.series_uid``                       DICOM UID              ``"unknown"``
study_date          ``study.date``                              ``YYYYMMDD``           ``"unknown"``
study_modalities    ``study.modalities_in_study``               sorted, ``_``-joined   ``"unknown"``
series_modality     ``series.modality``                         string                 ``"unknown"``
series_num          ``series.series_number``                    zero-padded 5 digits   ``"unknown"``
==================  =========================================  =====================  =============

Example template::

    {anon_patient_id}/{study_modalities}_{study_date}/{series_num}_{anon_series_uid}

renders to::

    CLARINET_42/CT_SR_20260415/00001_9.9.9.9.5
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
