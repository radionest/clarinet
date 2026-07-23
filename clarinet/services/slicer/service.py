"""SlicerService — orchestrates building and sending scripts to 3D Slicer."""

from pathlib import Path
from typing import Any

from clarinet.exceptions import SlicerError
from clarinet.services.slicer.client import SlicerClient
from clarinet.services.slicer.correspondence_bundle import build_correspondence_bundle
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
        *,
        include_correspondence: bool = False,
    ) -> dict[str, Any]:
        """Build and send a script to Slicer.

        The final payload is: helper code + context variables + user script.

        Args:
            slicer_url: Base URL of the target Slicer instance.
            script: User Python script to execute.
            context: Optional dict of variable assignments to inject.
            request_timeout: Optional HTTP timeout override (seconds).
            include_correspondence: When True, prepend the flattened
                correspondence-engine bundle (``build_overlap_graph`` et al.,
                see ``correspondence_bundle.py``) so the script can call it
                directly. Default False keeps scripts lean — most scripts
                don't need it.

        Returns:
            The script's ``__execResult`` dict (or ``{}`` if the script did not
            assign one). When called from ``_process_submission`` for a
            ``slicer_result_validator``, this dict is merged into ``record.data``
            — see ``clarinet/services/slicer/CLAUDE.md`` →
            "``__execResult`` Result-Merging Contract".
        """
        full_script = self._build_script(
            script, context, include_correspondence=include_correspondence
        )
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

    def _build_script(
        self,
        script: str,
        context: dict[str, Any] | None,
        include_correspondence: bool = False,
    ) -> str:
        """Combine helper + context + user script.

        Helper definitions (SlicerHelper, PacsHelper, etc.) stay in globals.
        The user script runs inside ``_run()`` via inner ``exec()`` in a flat
        per-call namespace (``_ns``, single dict = both globals and locals).
        This eliminates ``UnboundLocalError`` when user scripts shadow helper
        names (e.g. ``slicer = SlicerHelper(...)``), and lets the VTK C++
        objects (~1-3 GB per volume) it creates be GC'd when ``_ns`` goes out
        of scope, instead of accumulating in Slicer's reused exec() globals.

        **Context variables go into the module globals, not ``_ns``.** Helper
        functions such as ``_get_pacs_helper()`` read injected PACS params via
        ``globals()`` — whose ``__globals__`` is this outer (helper) namespace,
        NOT the per-call ``_ns`` the user script executes in. Injecting context
        only into ``_ns`` left it invisible to those helpers, so PACS context
        overrides silently fell back to ``from_slicer()``. ``_ns`` is built
        *after* the injection, so the user script still sees the values too.

        **Injected keys are removed in ``finally``.** Slicer reuses the exec
        namespace across calls, so without cleanup one call's context (record
        UIDs, file_registry paths, PACS params) would silently bleed into every
        later script — and into manual Slicer-console use, where the documented
        ``from_slicer()`` fallback must stay reachable. Helpers only run while
        the user script executes, so popping after ``exec`` loses nothing; the
        ``finally`` keeps a crashing script from leaving stale context behind.

        **``__execResult`` is dropped from the per-call ``_ns``, not in
        ``finally``.** The propagation line writes the result into module globals
        so Slicer can read it *after* the script finishes — cleaning it in
        ``finally`` would erase the channel before Slicer reads it. But the same
        write means the *next* call's ``_ns = dict(globals())`` inherits the
        previous result; without the pop, a script that assigns no
        ``__execResult`` returns the stale one via ``_ns.get(..., {})``, breaking
        the "``{}`` if the script did not assign one" contract.

        **``include_correspondence`` inserts the bundle BETWEEN helper and
        runner, never before the helper.** ``self._helper_source`` starts with
        (docstring, then ``from __future__ import annotations``) — Slicer runs
        Python 3.9, where that import must be the first statement of the exec
        unit or ``X | None`` annotations raise ``TypeError`` at definition
        time. ``build_correspondence_bundle()`` strips its per-module future
        imports (Task 1) and re-emits one of its own only for standalone exec,
        so it is requested here with ``standalone=False``: placing it after the
        helper keeps exactly one, legally positioned, future import in the
        composed script. Two would not merely be redundant — a future import
        after other statements is a ``SyntaxError``, so the whole script would
        fail to compile.
        """
        ctx_lines = "".join(
            f"    globals()[{key!r}] = {value!r}\n" for key, value in (context or {}).items()
        )
        ctx_keys = tuple(context) if context else ()
        runner = f"""\
def _run():
{ctx_lines}    _ns = dict(globals())
    _ns.pop('__execResult', None)
    try:
        exec(compile({script!r}, '<slicer_script>', 'exec'), _ns)
        globals()['__execResult'] = _ns.get('__execResult', {{}})
    finally:
        for _k in {ctx_keys!r}:
            globals().pop(_k, None)

_run()
del _run"""

        if include_correspondence:
            bundle = build_correspondence_bundle(standalone=False)
            return "\n".join([self._helper_source, "", bundle, "", runner])
        return "\n".join([self._helper_source, "", runner])
