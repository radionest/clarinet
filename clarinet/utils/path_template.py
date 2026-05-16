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

import re
from collections.abc import Mapping
from enum import Enum
from pathlib import PurePosixPath
from string import Formatter
from typing import Any

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


def extract_placeholders(template: str) -> set[str]:
    """Return the set of placeholder names referenced in ``template``.

    Used by ``build_context`` to compute only the values that the template
    actually references, so a raw-UID template (no ``{anon_*}``) never
    triggers anonymized-UID resolution. The returned set may contain names
    NOT in ``SUPPORTED_PLACEHOLDERS`` — call ``validate_template`` separately
    if you need to enforce the catalogue. Unnamed positional fields (``{}``)
    and escaped braces (``{{``/``}}``) are ignored.
    """
    return {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}


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


# ── Type-aware renderer (single source of truth for {placeholder} interpolation) ──
#
# The legacy `str.format_map` path can render a list as its Python repr
# (`"['CT', 'SR']"`), which then leaks into directory and file names.
# `render_template` coerces lists/tuples/sets to a sorted, separator-joined
# string before substitution, so a `record.data` field carrying a list is
# rendered as `"CT_SR"` instead.

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][\w.]*)\}")
_MISSING: Any = object()


class RenderMode(Enum):
    """Rendering mode for `render_template`."""

    STRICT = "strict"
    LENIENT = "lenient"


def coerce_field_value(
    value: Any,
    *,
    list_separator: str = "_",
    list_sorted: bool = True,
) -> str | None:
    """Coerce a placeholder value to a path-safe string.

    Returns ``None`` to signal "missing / empty" so the caller can decide
    strict-vs-lenient handling. Returns ``""`` only for an explicitly empty
    input string.

    Type dispatch via ``match``: ``bool`` MUST match before ``int`` because
    ``isinstance(True, int)`` is True in Python.
    """
    match value:
        case None:
            return None
        case bool():
            return "true" if value else "false"
        case str():
            return value
        case int() | float():
            return str(value)
        case Mapping():
            raise ValueError(f"cannot interpolate dict value: {value!r}")
        case list() | tuple() | set() | frozenset():
            parts = [str(v) for v in value if v is not None and str(v) != ""]
            if not parts:
                return None
            if list_sorted:
                parts = sorted(parts)
            return list_separator.join(parts)
        case _:
            return str(value)


def _resolve_dotted(fields: Mapping[str, Any], key: str) -> Any:
    """Walk a dotted path through nested Mappings.

    Returns the ``_MISSING`` sentinel if any step fails (missing key or
    non-Mapping intermediate). Returns ``None`` for an explicit ``None``
    value at the end of the path (so caller can distinguish "missing"
    from "present-but-null").
    """
    obj: Any = fields
    for part in key.split("."):
        if isinstance(obj, Mapping):
            if part not in obj:
                return _MISSING
            obj = obj[part]
        else:
            return _MISSING
        if obj is None:
            return None
    return obj


def render_template(
    template: str,
    fields: Mapping[str, Any],
    *,
    mode: RenderMode = RenderMode.LENIENT,
    list_separator: str = "_",
    list_sorted: bool = True,
    missing: str = "",
    on_missing_leave_as_is: bool = False,
) -> str:
    """Render ``template`` against ``fields`` with type-aware coercion.

    Recognised tokens have the form ``{name}`` or ``{a.b.c}`` (dotted-path
    walk through nested Mappings). The regex deliberately rejects Python
    format specifiers (``{val:0.2f}``) so a colon in a placeholder name is
    treated as a literal — no caller in Clarinet uses format specs today.

    Modes:
        STRICT — missing keys, ``None`` values, and empty collections raise
            ``KeyError(key)``. A ``dict`` value raises ``ValueError`` (a dict
            cannot be flattened to a path segment).
        LENIENT — missing keys substitute ``missing`` (default ``""``), or
            leave the literal ``{key}`` if ``on_missing_leave_as_is`` is True.
            ``None`` values, empty collections, and dict values substitute
            ``missing``.

    Safety checks (``/``, ``\\``, ``..``, leading ``.``) are NOT applied
    here — they belong to caller code that knows whether a rendered string
    is a single directory segment (where dots are forbidden) or a file
    basename (where ``.ext`` is legitimate).
    """

    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        raw = _resolve_dotted(fields, key)
        if raw is _MISSING:
            if mode is RenderMode.STRICT:
                raise KeyError(key)
            return m.group(0) if on_missing_leave_as_is else missing
        try:
            coerced = coerce_field_value(
                raw, list_separator=list_separator, list_sorted=list_sorted
            )
        except ValueError:
            if mode is RenderMode.STRICT:
                raise
            return missing
        if coerced is None:
            if mode is RenderMode.STRICT:
                raise KeyError(key)
            return missing
        return coerced

    return _PLACEHOLDER_RE.sub(_replace, template)
