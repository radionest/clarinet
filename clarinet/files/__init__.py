"""Public facade for on-disk path resolution and file access.

Only ``Files`` (and ``AnonPathError`` for ``except`` clauses) are public.
Lazy ``__getattr__`` keeps this package import-light so the stdlib-only
``clarinet.files._template`` leaf stays importable from ``clarinet.settings``
without dragging in models / services (avoids a bootstrap import cycle).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clarinet.exceptions.domain import AnonPathError
    from clarinet.files.facade import Files

__all__ = ["AnonPathError", "Files"]


def __getattr__(name: str) -> object:
    if name == "Files":
        from clarinet.files.facade import Files

        return Files
    if name == "AnonPathError":
        from clarinet.exceptions.domain import AnonPathError

        return AnonPathError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
