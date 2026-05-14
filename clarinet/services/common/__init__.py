"""Cross-cutting service helpers shared by multiple service layers.

Modules here are intentionally free of dependencies on optional or
heavyweight subsystems (``pipeline``/TaskIQ, ``slicer``/HTTP, etc.) so
that any service can import them without pulling broker startup,
network clients, or background workers into the import graph.
"""

from clarinet.services.common.file_resolver import FileResolver, resolve_pattern_from_dict

__all__ = ["FileResolver", "resolve_pattern_from_dict"]
