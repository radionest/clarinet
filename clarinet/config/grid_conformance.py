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
from clarinet.models.file_schema import FileRole
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from clarinet.models.record import RecordTypeCreate

# Coarser -> finer. Neither the declaring file nor its reference may be finer
# than the RecordType's own level, and a reference may not be finer than the
# file declaring it — any of those leaves a file with no working directory to
# resolve against.
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
    itself, must not itself declare ``grid_conform_to`` (chains and cycles are
    unsupported), neither side's effective level may be finer than the
    RecordType's own level nor the reference's finer than the declaring
    file's, neither side may be a collection, and both patterns must be
    grid-readable. A file that sets ``on_grid_mismatch`` without a reference
    is rejected too — the action could never run.

    Logs a ``WARNING`` (not an error) when an INPUT file references an OUTPUT
    of the same RecordType: legal, but it keeps the record blocked until that
    OUTPUT exists.

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
            if getattr(fd, "on_grid_mismatch", None):
                raise RecordConstraintViolationError(
                    f"RecordType '{rt.name}' file '{fd.name}' sets on_grid_mismatch="
                    f"'{fd.on_grid_mismatch}' without grid_conform_to — the action "
                    f"can never run; declare the reference or drop the action"
                )
            continue

        prefix = f"RecordType '{rt.name}' file '{fd.name}' grid_conform_to"

        if ref_name == fd.name:
            raise RecordConstraintViolationError(f"{prefix} references itself")

        ref = by_name.get(ref_name)
        if ref is None:
            raise RecordConstraintViolationError(
                f"{prefix}='{ref_name}' is unknown — no file of that name is "
                f"bound to this RecordType. Bound files: "
                f"{sorted(by_name) or '(none)'}. A file's declaration is shared "
                f"by every RecordType binding it, so each of them must bind the "
                f"reference too: add '{ref_name}' to this RecordType's files, or "
                f"fix the name if it is a typo"
            )

        if getattr(ref, "grid_conform_to", None):
            raise RecordConstraintViolationError(
                f"{prefix}='{ref_name}': the reference itself declares "
                f"grid_conform_to='{ref.grid_conform_to}' — chained conformance "
                f"declarations are not supported (enforcement order is undefined "
                f"and a repaired reference silently invalidates its dependents); "
                f"point both files at the same reference instead"
            )

        if getattr(fd, "multiple", False) or getattr(ref, "multiple", False):
            raise RecordConstraintViolationError(
                f"{prefix}='{ref_name}' involves a collection (multiple=True); "
                f"grid conformance is defined for singular files only"
            )

        fd_level = (fd.level or rt.level).value
        ref_level = (ref.level or rt.level).value
        rt_level = rt.level.value
        if _LEVEL_ORDER[fd_level] > _LEVEL_ORDER[rt_level]:
            raise RecordConstraintViolationError(
                f"{prefix}='{ref_name}': '{fd.name}' has level ({fd_level}) finer "
                f"than this RecordType's own level ({rt_level}); it cannot be "
                f"resolved for a record at that level"
            )
        if _LEVEL_ORDER[ref_level] > _LEVEL_ORDER[rt_level]:
            raise RecordConstraintViolationError(
                f"{prefix}='{ref_name}' has level ({ref_level}) finer than this "
                f"RecordType's own level ({rt_level}); it cannot be resolved for "
                f"a record at that level"
            )
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

        if (
            getattr(fd, "role", None) == FileRole.INPUT
            and getattr(ref, "role", None) == FileRole.OUTPUT
        ):
            logger.warning(
                f"RecordType '{rt.name}': INPUT '{fd.name}' declares grid_conform_to="
                f"'{ref_name}', which is bound as an OUTPUT of the same type — the "
                f"record stays blocked until that OUTPUT exists (typically written "
                f"by a pipeline before check-files). Confirm this is intended."
            )
