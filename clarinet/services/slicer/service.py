"""SlicerService — orchestrates building and sending scripts to 3D Slicer."""

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


class SlicerService:
    """Orchestrates building and sending scripts to Slicer.

    Reads ``helper.py`` source code once on init, then prepends it (along with
    any context variables) to every user script before sending via
    :class:`SlicerClient`.
    """

    def __init__(self) -> None:
        """Read and cache ``helper.py`` source code."""
        self._helper_source: str = _HELPER_PATH.read_text(encoding="utf-8")
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
        Context variables and user script run inside ``_run()`` via inner
        ``exec()`` in a flat namespace (single dict = both globals and locals).
        This eliminates ``UnboundLocalError`` when user scripts shadow helper
        names (e.g. ``slicer = SlicerHelper(...)``), because a flat namespace
        has no local-vs-global distinction — unlike a function scope where
        any assignment marks the name as local for the entire body.

        The ``_run()`` wrapper ensures VTK C++ objects (~1-3 GB per volume)
        are GC'd when the namespace dict goes out of scope, instead of
        accumulating in Slicer's reused exec() global namespace.
        """
        parts = [self._helper_source, ""]

        indent = "    "
        run_lines = ["def _run():"]
        run_lines.append(f"{indent}_ns = dict(globals())")
        if context:
            for key, value in context.items():
                run_lines.append(f"{indent}_ns[{key!r}] = {value!r}")
        run_lines.append(f"{indent}exec(compile({script!r}, '<slicer_script>', 'exec'), _ns)")
        run_lines.append(f"{indent}globals()['__execResult'] = _ns.get('__execResult', {{}})")
        run_lines.extend(["", "_run()", "del _run"])

        parts.append("\n".join(run_lines))
        return "\n".join(parts)
