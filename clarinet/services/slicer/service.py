"""SlicerService — orchestrates building and sending scripts to 3D Slicer."""

import ast
from pathlib import Path
from typing import Any

from clarinet.exceptions import SlicerError
from clarinet.services.slicer.client import SlicerClient
from clarinet.settings import settings
from clarinet.utils.logger import logger

# Path to the helper module that gets prepended to user scripts
_HELPER_PATH = Path(__file__).parent / "helper.py"

# Standalone Slicer cleanup script — mirrors SlicerHelper.__init__ cleanup
# but without requiring SlicerHelper to be defined on the Slicer side.
# No processEvents(): the composite-node detach removes stale volume refs
# before Clear(0), so no VTK warnings are queued and a re-entrant Qt drain
# (this script runs inside Slicer's HTTP handler) is unnecessary.
_RESET_SCENE_SCRIPT = """
import slicer
lm = slicer.app.layoutManager()
for name in ("Red", "Yellow", "Green"):
    widget = lm.sliceWidget(name)
    if widget is None:
        continue
    composite = widget.mrmlSliceCompositeNode()
    composite.SetBackgroundVolumeID(None)
    composite.SetForegroundVolumeID(None)
    composite.SetLabelVolumeID(None)
slicer.mrmlScene.Clear(0)
__execResult = {"cleaned": True}
"""


def _extract_top_level_names(source: str) -> list[str]:
    """Extract top-level class, function, and variable names from Python source.

    Used to generate ``global`` declarations inside ``_run()`` so that
    user scripts wrapped in the function scope can still access (and even
    shadow) helper-level names without triggering ``UnboundLocalError``.
    """
    names: set[str] = set()
    for node in ast.iter_child_nodes(ast.parse(source)):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return sorted(names)


class SlicerService:
    """Orchestrates building and sending scripts to Slicer.

    Reads ``helper.py`` source code once on init, then prepends it (along with
    any context variables) to every user script before sending via
    :class:`SlicerClient`.
    """

    def __init__(self) -> None:
        """Read and cache ``helper.py`` source code."""
        self._helper_source: str = _HELPER_PATH.read_text(encoding="utf-8")
        self._helper_globals: list[str] = _extract_top_level_names(self._helper_source)
        # DEBUG, not INFO: SlicerService is created per-request via Depends,
        # so an INFO line on every instantiation would flood the log.
        logger.debug(
            f"SlicerService init: helper.py loaded, "
            f"size={len(self._helper_source)}B lines={self._helper_source.count(chr(10))}"
        )

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

    async def reset_scene(
        self,
        slicer_url: str,
        request_timeout: float = 5.0,
    ) -> None:
        """Reset Slicer scene state — detach views and clear scene.

        Fails silently with a warning log on **any** Slicer error — pre-cleanup
        should never block the primary request. Intended to be called before
        a fresh workflow (e.g. user switching to a different record).

        Catches the full ``SlicerError`` hierarchy (connection, HTTP 500,
        script execution): a fresh Slicer instance whose ``layoutManager``
        is not yet initialised, for example, returns 500 — but failure here
        must still be non-blocking.

        Args:
            slicer_url: Base URL of the target Slicer instance.
            request_timeout: HTTP timeout override (default 5s — cleanup
                should be fast; if it hangs, something is already very wrong).
        """
        try:
            await self.execute_raw(slicer_url, _RESET_SCENE_SCRIPT, request_timeout)
        except SlicerError as e:
            logger.warning(f"Slicer scene reset failed (non-blocking): {e}")

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
        """Combine helper + context + user script.

        Helper definitions (SlicerHelper, PacsHelper, etc.) stay in globals.
        Context variables and user script are wrapped in ``def _run()`` so
        all user variables are function-local and get GC'd on return.
        This prevents VTK C++ objects (volumes ~1-3 GB) from accumulating
        in Slicer's reused exec() global namespace across requests.
        """
        parts = [self._helper_source, ""]

        # Wrap context + user script in a function for local scope.
        indent = "    "
        # Declare helper names as global so _run() can access (and shadow)
        # them without UnboundLocalError — user scripts may do
        # ``SlicerHelper = SlicerHelper(...)`` which makes the name local.
        global_names = ", ".join(["__execResult", *self._helper_globals])
        run_lines = ["def _run():", f"{indent}global {global_names}"]
        if context:
            for line in self._build_context_block(context).split("\n"):
                run_lines.append(f"{indent}{line}")
            run_lines.append("")
        for line in script.split("\n"):
            run_lines.append(f"{indent}{line}" if line.strip() else "")
        run_lines.extend(["", "_run()", "del _run"])

        parts.append("\n".join(run_lines))
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
