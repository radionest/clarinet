"""Async DICOM client — dimsechord's SCU façade plus mode-aware retrieval.

``dimsechord.DicomClient`` retrieves with C-GET only. Clarinet additionally
supports ``dicom_retrieve_mode="c-move"``, where the PACS is asked to send the
instances to our own Storage SCP (move-to-self) — the only option against a
peer that does not offer C-GET. This subclass keeps every dimsechord method as
is and overrides the four ``get_*`` entry points so that a single setting, not
the call site, decides which transport runs.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from dimsechord import DicomClient as DimsechordClient

from clarinet.settings import settings
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from dimsechord import DicomNode, RetrieveResult

#: Floor for the arrival wait, so a C-MOVE that returns just as the budget
#: expires still gets a moment for its last C-STOREs to land.
_MIN_ARRIVAL_WAIT = 1.0

#: How often the c-move progress poller samples the receiving session.
_PROGRESS_INTERVAL = 0.5


def _is_move_mode() -> bool:
    return settings.dicom_retrieve_mode in ("c-move", "c-move-study")


def _write_instances(instances: dict[str, Any], output_dir: Path) -> None:
    """Persist received instances as ``{sop_uid}.dcm``. Runs off the event loop."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for sop_uid, dataset in instances.items():
        dataset.save_as(output_dir / f"{sop_uid}.dcm", enforce_file_format=True)


class DicomClient(DimsechordClient):
    """DICOM SCU with Clarinet's ``dicom_retrieve_mode`` dispatch.

    In a c-get mode every ``get_*`` call falls through to dimsechord. In a
    c-move mode they instead run C-MOVE-to-self against the Storage SCP
    singleton, which must already be listening (the API lifespan and the
    ``--dicom`` worker start it).
    """

    async def get_study(
        self,
        study_uid: str,
        peer: DicomNode,
        output_dir: Path,
        *,
        timeout: float = 300.0,  # noqa: ASYNC109 — DICOM association timeout, not asyncio
    ) -> RetrieveResult:
        """Retrieve a study to disk, via C-GET or C-MOVE-to-self."""
        if _is_move_mode():
            return await self._retrieve_via_move(
                study_uid=study_uid,
                series_uid=None,
                peer=peer,
                output_dir=output_dir,
                timeout=timeout,
            )
        return await super().get_study(study_uid, peer, output_dir, timeout=timeout)

    async def get_series(
        self,
        study_uid: str,
        series_uid: str,
        peer: DicomNode,
        output_dir: Path,
        *,
        timeout: float = 300.0,  # noqa: ASYNC109 — DICOM association timeout, not asyncio
    ) -> RetrieveResult:
        """Retrieve a series to disk, via C-GET or C-MOVE-to-self."""
        if _is_move_mode():
            return await self._retrieve_via_move(
                study_uid=study_uid,
                series_uid=series_uid,
                peer=peer,
                output_dir=output_dir,
                timeout=timeout,
            )
        return await super().get_series(study_uid, series_uid, peer, output_dir, timeout=timeout)

    async def get_study_to_memory(
        self,
        study_uid: str,
        peer: DicomNode,
        *,
        timeout: float = 300.0,  # noqa: ASYNC109 — DICOM association timeout, not asyncio
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> RetrieveResult:
        """Retrieve a study to memory, via C-GET or C-MOVE-to-self."""
        if _is_move_mode():
            return await self._retrieve_via_move(
                study_uid=study_uid,
                series_uid=None,
                peer=peer,
                output_dir=None,
                timeout=timeout,
                on_progress=on_progress,
            )
        return await super().get_study_to_memory(
            study_uid, peer, timeout=timeout, on_progress=on_progress
        )

    async def get_series_to_memory(
        self,
        study_uid: str,
        series_uid: str,
        peer: DicomNode,
        *,
        timeout: float = 300.0,  # noqa: ASYNC109 — DICOM association timeout, not asyncio
    ) -> RetrieveResult:
        """Retrieve a series to memory, via C-GET or C-MOVE-to-self."""
        if _is_move_mode():
            return await self._retrieve_via_move(
                study_uid=study_uid,
                series_uid=series_uid,
                peer=peer,
                output_dir=None,
                timeout=timeout,
            )
        return await super().get_series_to_memory(study_uid, series_uid, peer, timeout=timeout)

    async def _retrieve_via_move(
        self,
        *,
        study_uid: str,
        series_uid: str | None,
        peer: DicomNode,
        output_dir: Path | None,
        timeout: float,  # noqa: ASYNC109 — DICOM association timeout, not asyncio
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> RetrieveResult:
        """C-MOVE-to-self: ask the peer to C-STORE the instances to our SCP.

        The C-MOVE reports how many sub-operations it completed, but the
        instances themselves arrive on a separate association, so the peer's
        tally is not proof of arrival: the SCP session is told how many the
        peer *announced* — completed plus failed, warned and still-remaining —
        and then waited on. ``settings.dicom_cmove_timeout`` bounds the move
        and the arrival wait together, measured from registration.

        Returns:
            The C-MOVE result, with ``instances`` / ``num_completed`` taken from
            what physically arrived, and ``status="timeout"`` if the wait ran out
            before the last instance did.

        Raises:
            RuntimeError: If the Storage SCP is not listening.
        """
        from clarinet.services.dicom.scp import get_storage_scp

        scp = get_storage_scp()
        if not scp.is_running:
            raise RuntimeError(
                f"Storage SCP not running — dicom_retrieve_mode="
                f"{settings.dicom_retrieve_mode!r} needs one to receive the "
                f"C-STORE sub-operations. Start the API server or the worker "
                f"(both start it for a c-move mode), and make sure the PACS "
                f"routes AET {settings.dicom_aet!r} back to port "
                f"{settings.dicom_port}. Set dicom_retrieve_mode='c-get' if it "
                f"cannot reach us."
            )

        label = "series" if series_uid else "study"
        key = f"{study_uid}/{series_uid or ''}"
        session = scp.register_session(key, collect=True)
        started = time.monotonic()
        logger.debug(f"Retrieving {label} {series_uid or study_uid} via C-MOVE to self")

        try:
            poller = (
                asyncio.create_task(self._poll_progress(session, on_progress))
                if on_progress is not None
                else None
            )
            try:
                if series_uid is None:
                    result = await super().move_study(
                        study_uid, peer, settings.dicom_aet, timeout=timeout
                    )
                else:
                    result = await super().move_series(
                        study_uid, series_uid, peer, settings.dicom_aet, timeout=timeout
                    )
            finally:
                if poller is not None:
                    poller.cancel()
                    await asyncio.gather(poller, return_exceptions=True)

            # The peer's *announced* total, not just what it says it completed.
            # A move aborted mid-stream still carries the sub-operations it had
            # left in the last response it sent, so the arrival target survives
            # a partial transfer — taking num_completed alone would declare the
            # truncated set complete.
            scp.set_expected(
                key,
                result.num_completed
                + result.num_failed
                + result.num_warning
                + result.num_remaining,
            )
            elapsed = time.monotonic() - started
            remaining = max(settings.dicom_cmove_timeout - elapsed, _MIN_ARRIVAL_WAIT)
            arrived = await asyncio.to_thread(scp.wait_for_completion, key, remaining)

            finished = scp.finish_session(key)
            if finished is None:
                return result

            result.instances = finished.instances
            result.num_completed = finished.received_count

            if not arrived:
                logger.warning(
                    f"C-MOVE timed out: received {finished.received_count}/"
                    f"{finished.expected_count if finished.expected_count is not None else 'unknown'} "
                    f"instances for {label} {series_uid or study_uid}"
                )
                result.status = "timeout"

            if output_dir is not None:
                await asyncio.to_thread(_write_instances, finished.instances, output_dir)

            if on_progress is not None:
                on_progress(finished.received_count, finished.expected_count)

            logger.info(f"C-MOVE retrieve complete: {finished.received_count} instances received")
            return result
        finally:
            # Idempotent: a no-op when the success path already finished it,
            # the cleanup that matters when the move raised.
            scp.finish_session(key)

    @staticmethod
    async def _poll_progress(
        session: Any,
        on_progress: Callable[[int, int | None], None],
    ) -> None:
        """Report arrivals while the C-MOVE runs.

        dimsechord's ``move_*`` exposes no per-sub-operation hook, so progress
        is sampled from the receiving session instead of being driven by the
        C-MOVE pending responses. ``expected_count`` is only known once the move
        returns, so ``total`` stays ``None`` for the duration.
        """
        last = -1
        while True:
            await asyncio.sleep(_PROGRESS_INTERVAL)
            received = session.received_count
            if received != last:
                last = received
                on_progress(received, session.expected_count)
