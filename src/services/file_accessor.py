"""
File accessor service for typed file access in pipeline tasks.

Provides attribute-based access to files defined in a RecordType's file_registry,
supporting both singular files and collections (glob patterns).
"""

from pathlib import Path

from src.models.file_schema import FileDefinition
from src.models.record import RecordRead
from src.utils.file_patterns import PLACEHOLDER_REGEX, resolve_pattern


class RecordFileAccessor:
    """Typed file accessor for records.

    Provides attribute-based access to files defined in the record type's
    file_registry. Singular files resolve to a single Path; collections
    (multiple=True) resolve to a sorted list of Paths via glob.

    Args:
        record: RecordRead instance with record_type populated
        working_folder: Override for working folder path

    Examples:
        >>> accessor = RecordFileAccessor(record)
        >>> accessor.lung_mask  # Path to singular file
        >>> accessor.user_segmentation  # list[Path] for multiple=True
        >>> accessor.path_for("output")  # Path with parent dirs created
    """

    def __init__(self, record: RecordRead, working_folder: str | Path | None = None) -> None:
        self._record = record
        folder = working_folder or record.working_folder
        if folder is None:
            raise ValueError("No working folder available for record")
        self._working_dir = Path(folder)
        self._registry: dict[str, FileDefinition] = {}
        for fd in record.record_type.file_registry or []:
            if isinstance(fd, dict):
                fd = FileDefinition.model_validate(fd)
            self._registry[fd.name] = fd

    def __getattr__(self, name: str) -> Path | list[Path]:
        """Access a file by its registry name.

        Args:
            name: File definition name from file_registry

        Returns:
            Path for singular files, list[Path] for collections

        Raises:
            AttributeError: If name is not in the file registry
        """
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            fd = self._registry[name]
        except KeyError:
            raise AttributeError(
                f"No file definition '{name}' in registry. Available: {list(self._registry.keys())}"
            ) from None
        if fd.multiple:
            return self._glob(fd)
        return self._resolve(fd)

    def _resolve(self, fd: FileDefinition) -> Path:
        """Resolve singular file pattern to Path."""
        filename = resolve_pattern(fd.pattern, self._record)
        return self._working_dir / filename

    def _glob(self, fd: FileDefinition) -> list[Path]:
        """Glob collection pattern, replacing all {placeholders} with *."""
        glob_pattern = PLACEHOLDER_REGEX.sub("*", fd.pattern)
        return sorted(self._working_dir.glob(glob_pattern))

    def path_for(self, name: str) -> Path:
        """Get write path for a file (singular only). Creates parent dirs.

        Args:
            name: File definition name from file_registry

        Returns:
            Resolved Path with parent directories created

        Raises:
            KeyError: If name is not in the file registry
        """
        fd = self._registry[name]
        path = self._resolve(fd)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def available(self) -> list[str]:
        """List available file definition names."""
        return list(self._registry.keys())


def get_file_accessor(
    record: RecordRead,
    working_folder: str | Path | None = None,
) -> RecordFileAccessor:
    """Factory function to create a RecordFileAccessor.

    Args:
        record: RecordRead instance with record_type populated
        working_folder: Override for working folder path

    Returns:
        RecordFileAccessor instance
    """
    return RecordFileAccessor(record, working_folder)
