"""SlicerService — orchestrates building and sending scripts to 3D Slicer."""

from pathlib import Path
from typing import Any

from src.services.slicer.client import SlicerClient
from src.settings import settings
from src.utils.logger import logger

# Path to the helper module that gets prepended to user scripts
_HELPER_PATH = Path(__file__).parent / "helper.py"


class SlicerService:
    """Orchestrates building and sending scripts to Slicer.

    Reads ``helper.py`` source code once on init, then prepends it (along with
    any context variables) to every user script before sending via
    :class:`SlicerClient`.
    """

    def __init__(self) -> None:
        """Read and cache ``helper.py`` source code."""
        self._helper_source: str = _HELPER_PATH.read_text(encoding="utf-8")

    async def execute(
        self,
        slicer_url: str,
        script: str,
        context: dict[str, Any] | None = None,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        """Build and send a script to Slicer.

        The final payload is: helper code + context variables + user script.

        Args:
            slicer_url: Base URL of the target Slicer instance.
            script: User Python script to execute.
            context: Optional dict of variable assignments to inject.
            request_timeout: Optional HTTP timeout override (seconds).

        Returns:
            JSON response from Slicer.
        """
        full_script = self._build_script(script, context)
        logger.debug(f"Sending script to Slicer at {slicer_url} ({len(full_script)} chars)")
        return await self._send(slicer_url, full_script, request_timeout)

    async def execute_raw(
        self,
        slicer_url: str,
        script: str,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a script without prepending the helper.

        Args:
            slicer_url: Base URL of the target Slicer instance.
            script: Raw Python script to execute.
            request_timeout: Optional HTTP timeout override (seconds).

        Returns:
            JSON response from Slicer.
        """
        return await self._send(slicer_url, script, request_timeout)

    async def ping(self, slicer_url: str) -> bool:
        """Check if a Slicer web server is reachable.

        Args:
            slicer_url: Base URL of the target Slicer instance.

        Returns:
            True if Slicer responds successfully.
        """
        async with SlicerClient(slicer_url, timeout=settings.slicer_timeout) as client:
            return await client.ping()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _send(
        self, slicer_url: str, script: str, request_timeout: float | None
    ) -> dict[str, Any]:
        """Open a short-lived client and send the script."""
        effective_timeout = (
            request_timeout if request_timeout is not None else settings.slicer_timeout
        )
        async with SlicerClient(slicer_url, timeout=effective_timeout) as client:
            return await client.execute(script)

    def _build_script(self, script: str, context: dict[str, Any] | None) -> str:
        """Combine helper + context + user script."""
        parts = [self._helper_source, ""]
        if context:
            parts.append(self._build_context_block(context))
            parts.append("")
        parts.append(script)
        return "\n".join(parts)

    @staticmethod
    def _build_context_block(context: dict[str, Any]) -> str:
        """Generate variable assignment lines from a context dict.

        Handles str, int, float, bool, list, dict types safely.

        Args:
            context: Mapping of variable names to values.

        Returns:
            Multi-line string of Python assignments.
        """
        lines: list[str] = ["# --- context variables ---"]
        for key, value in context.items():
            lines.append(f"{key} = {value!r}")
        return "\n".join(lines)
