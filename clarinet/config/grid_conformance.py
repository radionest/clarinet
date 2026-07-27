"""Config-load validation: grid-conformance declarations must be resolvable.

A ``FileDefinition`` may declare that its on-disk voxel grid must match
another registered file's (``grid_conform_to``). The runtime check resolves
both files through the record's own file registry, so a declaration that
cannot be resolved there degrades into something worse than an error: an
unbound reference raises ``KeyError`` from ``Files``' registry lookup, and a
finer-level reference has no working directory at all — ``Files.resolve``
raises, ``Files.checksums`` silently skips, and ``FileValidator.validate``
silently falls back to the record's own directory. This module is the static
check that catches those at config-load time instead.
"""

from typing import TYPE_CHECKING, Any

from clarinet.exceptions.domain import RecordConstraintViolationError

if TYPE_CHECKING:
    from clarinet.models.record import RecordTypeCreate

# Coarser -> finer. A reference must never be finer than the file declaring it:
# the declaring file's working directory would have no counterpart for it.
_LEVEL_ORDER = {"PATIENT": 0, "STUDY": 1, "SERIES": 2}

# Extensions ``read_grid`` can classify. ``.seg.nrrd`` is covered by ``.nrrd``.
_GRID_READABLE = (".nii", ".nii.gz", ".nrrd")


def _is_grid_readable(pattern: str) -> bool:
    """Whether *pattern* names a file format whose grid can be read."""
    return pattern.lower().endswith(_GRID_READABLE)


def validate_grid_conformance(rt: "RecordTypeCreate | Any") -> None:
    """Reject grid-conformance declarations that cannot be enforced at runtime.

    For every file in the RecordType's registry that sets ``grid_conform_to``:
    the reference must be bound to the same RecordType, must not be the file
    itself, must not be coarser-record-unresolvable (finer level), neither side
    may be a collection, and both patterns must be grid-readable.

    Args:
        rt: A ``RecordTypeCreate``-shaped object exposing ``name``, ``level``
            and ``file_registry`` (falsy/absent means nothing to check).

    Raises:
        RecordConstraintViolationError: Naming the RecordType, the declaring
            file, and what is wrong with the declaration.
    """
    registry = list(getattr(rt, "file_registry", None) or [])
    by_name = {fd.name: fd for fd in registry}

    for fd in registry:
        ref_name = getattr(fd, "grid_conform_to", None)
        if not ref_name:
            continue

        prefix = f"RecordType '{rt.name}' file '{fd.name}' grid_conform_to"

        if ref_name == fd.name:
            raise RecordConstraintViolationError(f"{prefix} references itself")

        ref = by_name.get(ref_name)
        if ref is None:
            raise RecordConstraintViolationError(
                f"{prefix}='{ref_name}' is unknown — no file of that name is "
                f"bound to this RecordType. Bound files: "
                f"{sorted(by_name) or '(none)'}"
            )

        if getattr(fd, "multiple", False) or getattr(ref, "multiple", False):
            raise RecordConstraintViolationError(
                f"{prefix}='{ref_name}' involves a collection (multiple=True); "
                f"grid conformance is defined for singular files only"
            )

        fd_level = (fd.level or rt.level).value
        ref_level = (ref.level or rt.level).value
        if _LEVEL_ORDER[ref_level] > _LEVEL_ORDER[fd_level]:
            raise RecordConstraintViolationError(
                f"{prefix}='{ref_name}' has a finer level ({ref_level}) than the "
                f"declaring file ({fd_level}); it cannot be resolved for a record "
                f"at the coarser level"
            )

        for role_label, candidate in (("file", fd), ("reference", ref)):
            if not _is_grid_readable(candidate.pattern):
                raise RecordConstraintViolationError(
                    f"{prefix}='{ref_name}': {role_label} pattern "
                    f"'{candidate.pattern}' is not a readable image format "
                    f"(expected one of {', '.join(_GRID_READABLE)})"
                )
