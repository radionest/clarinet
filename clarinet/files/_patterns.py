"""File pattern primitives and the unified placeholder-field builder.

Provides:
- ``PLACEHOLDER_REGEX`` — compiled regex for ``{placeholder}`` tokens
- ``_PatternedFile`` — duck-typed Protocol for file definitions with a pattern
- ``resolve_origin_type`` — inverted virtual-field resolver for ``origin_type``
- ``glob_file_paths`` — replace placeholders with wildcards and glob a directory
- ``fields_from`` — canonical placeholder dict for a record (unifies legacy sources)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from clarinet.models.record import RecordRead


class _PatternedFile(Protocol):
    """Duck-typed file definition with a placeholder pattern.

    Both ``FileDefinitionRead`` (DB-backed DTO) and ``FileDef`` (config primitive)
    satisfy this protocol — only ``pattern`` is required for globbing.
    """

    pattern: str


# Same placeholder grammar as ``_template.render_template`` so a collection's
# glob wildcards and a singular file's render agree on what counts as a
# placeholder — a name the renderer would reject (e.g. a leading digit) is not
# silently wildcarded by glob while left literal by resolve/checksums.
PLACEHOLDER_REGEX = re.compile(r"\{([a-zA-Z_][\w.]*)\}")


def resolve_origin_type(record: RecordRead, parent: RecordRead | None = None) -> str:
    """Resolve the ``origin_type`` virtual field for a record.

    Returns the parent's record type name when a parent is available,
    falling back to the record's own type name otherwise.

    Args:
        record: The current record.
        parent: Optional parent record.

    Returns:
        Record type name to use as ``origin_type``.
    """
    if parent is not None:
        return parent.record_type.name
    return record.record_type.name


def glob_file_paths(
    fd: _PatternedFile,
    working_dir: Path,
) -> list[Path]:
    """Glob collection file pattern, replacing placeholders with wildcards.

    Args:
        fd: File definition with pattern (should have multiple=True)
        working_dir: Base directory to glob in

    Returns:
        Sorted list of matching Paths
    """
    glob_pattern = PLACEHOLDER_REGEX.sub("*", fd.pattern)
    return sorted(working_dir.glob(glob_pattern))


def fields_from(record: RecordRead, parent: RecordRead | None = None) -> dict[str, Any]:
    """Canonical placeholder dict for a record.

    Canonical placeholder dict used by ``Files.resolve``, ``Files.render_for``,
    and the slicer / pipeline context. Scalar placeholders fall back to *parent*
    when the record's own value is missing/empty; ``origin_type`` uses the
    inverted virtual-field priority via :func:`resolve_origin_type`; the
    ``data`` sub-dict is parent-then-record merged for ``{data.FIELD}`` access.
    That dict-merge means a present-but-empty ``record.data[FIELD]`` wins its key
    and does NOT fall back to ``parent`` — unlike the scalar fields below, which
    fall back when the record's own value is ``None`` / ``""``.
    ``parent_id`` is a direct passthrough of ``record.parent_record_id`` — no
    parent-fallback and no attribute access on *parent* at all, so it renders
    identically whether or not the parent record was loaded.
    Coercion (lists → ``"CT_SR"``) happens later in ``_template.render``.
    """

    def scalar(name: str) -> Any:
        value = getattr(record, name, None)
        if value in (None, "") and parent is not None:
            value = getattr(parent, name, None)
        return value

    data = {**(getattr(parent, "data", None) or {}), **(getattr(record, "data", None) or {})}
    return {
        "id": record.id,
        "parent_id": record.parent_record_id,
        "user_id": scalar("user_id"),
        "patient_id": scalar("patient_id"),
        "study_uid": scalar("study_uid"),
        "series_uid": scalar("series_uid"),
        "record_type": {"name": record.record_type.name},
        "data": data,
        "origin_type": resolve_origin_type(record, parent),
    }
