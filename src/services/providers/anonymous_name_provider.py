"""Provider for anonymous names from external file."""

import aiofiles

from src.utils.logger import logger


class AnonymousNameProvider:
    """Provider for managing anonymous names from a file."""

    def __init__(self, names_file_path: str | None = None):
        """Initialize the provider with optional file path.

        Args:
            names_file_path: Path to the file containing anonymous names
        """
        self.names_file_path = names_file_path
        self._names_cache: list[str] | None = None

    async def get_available_names(self) -> list[str]:
        """Get list of available anonymous names.

        Returns:
            List of available names, empty list if file not found or error
        """
        if not self.names_file_path:
            return []

        if self._names_cache is None:
            await self._load_names()

        return self._names_cache or []

    async def _load_names(self) -> None:
        """Load names from file into cache."""
        if not self.names_file_path:
            self._names_cache = []
            return

        try:
            async with aiofiles.open(self.names_file_path) as f:
                content = await f.read()
                self._names_cache = content.strip().split("\n")
        except Exception as e:
            logger.warning(f"Failed to load anonymous names list: {e}")
            self._names_cache = []

    def clear_cache(self) -> None:
        """Clear the names cache to force reload on next access."""
        self._names_cache = None
