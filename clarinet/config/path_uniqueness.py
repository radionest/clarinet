"""Config-load validation: OUTPUT patterns must discriminate coexisting records.

A RecordType can allow several records to coexist at the same DICOM level
(``unique_by`` partitions, ``max_records`` quotas, ``parent_required`` fan-out).
Whenever that happens, every non-collection OUTPUT file must resolve to a
distinct on-disk path per coexisting record, or two records silently overwrite
each other's file. This module is the static check that catches an OUTPUT
pattern missing the placeholder needed to discriminate — at config-load time
(TOML/Python) and at RecordType create/update time — instead of at first
collision.
"""

from typing import TYPE_CHECKING, Any

from clarinet.exceptions.domain import RecordConstraintViolationError
from clarinet.files import PLACEHOLDER_REGEX
from clarinet.models.file_schema import FileRole

if TYPE_CHECKING:
    from clarinet.models.record import RecordTypeCreate

# DICOM level ordering (coarser -> finer) and the placeholder that identifies
# a RecordType's own level, used by the "coarser file level" rule below.
_LEVEL_UID = {"PATIENT": None, "STUDY": "study_uid", "SERIES": "series_uid"}
_LEVEL_ORDER = {"PATIENT": 0, "STUDY": 1, "SERIES": 2}


def _placeholders(pattern: str) -> set[str]:
    """Return the set of ``{placeholder}`` names in *pattern*."""
    return set(PLACEHOLDER_REGEX.findall(pattern))


def validate_output_path_uniqueness(rt: "RecordTypeCreate | Any") -> None:
    """Reject OUTPUT file patterns that cannot discriminate coexisting records.

    For every non-collection (``multiple=False``) OUTPUT file that has not
    opted out via ``allow_path_collision``, checks that the pattern embeds
    whichever placeholder is needed to keep coexisting records from resolving
    to the same path:

    - ``{id}`` anywhere in the pattern always passes (a record's own id is
      globally unique) — fast path, skips every other check.
    - ``"user"`` in ``unique_by`` requires ``{user_id}`` (distinct users may
      each hold their own record).
    - ``"parent"`` in ``unique_by`` + ``parent_required=True`` requires
      ``{parent_id}`` (distinct parents may each hold their own record;
      ``{origin_type}`` only distinguishes parent *types*, not instances).
    - An OUTPUT file whose own ``level`` is coarser than the RecordType's
      level requires the RecordType's own level UID placeholder (e.g. a
      SERIES-level type writing to a PATIENT-level file needs
      ``{series_uid}`` to avoid collisions between sibling series).
    - ``unique_by=None`` (no uniqueness constraint) with a quota that allows
      more than one coexisting record (``max_records is None`` or ``> 1``)
      requires ``{id}`` — nothing else distinguishes the rows.

    Args:
        rt: A ``RecordTypeCreate``-shaped object exposing ``unique_by``,
            ``parent_required``, ``max_records``, ``level``, ``name``, and
            ``file_registry`` (list of ``FileDefinitionRead``-shaped objects,
            or falsy/absent — treated as no files to check).

    Raises:
        RecordConstraintViolationError: Naming the RecordType and the
            offending OUTPUT file, listing which placeholder(s) are missing.
    """
    parts = rt.unique_by  # frozenset | None
    for fd in getattr(rt, "file_registry", None) or []:
        if fd.role != FileRole.OUTPUT or getattr(fd, "multiple", False):
            continue
        if getattr(fd, "allow_path_collision", False):
            continue  # per-file opt-out: author-guaranteed unique; siblings stay checked

        ph = _placeholders(fd.pattern)
        if "id" in ph:
            continue  # record.id is globally unique -> always safe

        missing: list[str] = []
        if parts and "user" in parts and "user_id" not in ph:
            missing.append("user -> {user_id}")
        if parts and "parent" in parts and rt.parent_required and "parent_id" not in ph:
            # {origin_type} distinguishes parent *types*, not two same-type
            # parents — only the per-file opt-out excuses relying on it.
            missing.append("parent -> {parent_id}")

        # A coarser file level is shared by every finer-grained sibling record
        # under it, so it must be disambiguated by the RecordType's own UID.
        fd_level = fd.level or rt.level
        if _LEVEL_ORDER[fd_level] < _LEVEL_ORDER[rt.level]:
            uid = _LEVEL_UID[rt.level]
            if uid and uid not in ph:
                missing.append(f"level -> {{{uid}}}")

        # No uniqueness constraint at all + a quota that allows 2+ coexisting
        # records -> nothing distinguishes rows but {id}.
        if parts is None and (rt.max_records is None or rt.max_records > 1):
            missing.append("no-uniqueness -> {id}")

        if missing:
            raise RecordConstraintViolationError(
                f"RecordType '{rt.name}' OUTPUT file '{fd.name}' pattern "
                f"'{fd.pattern}' cannot distinguish coexisting records: missing {missing}"
            )
