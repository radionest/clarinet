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
from typing import TYPE_CHECKING

from dimsechord import DicomClient as DimsechordClient
from dimsechord import QueryRetrieveLevel

# Internal to dimsechord, and imported deliberately: they are the argument
# types of ``DicomOperations.retrieve_via_move``, which has no async wrapper on
# the public client yet. Re-deriving the move-to-self dance here instead is what
# this module used to do, and it got the arrival target wrong twice.
from dimsechord._models import RetrieveRequest, StorageConfig, StorageMode

from clarinet.settings import settings

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from dimsechord import DicomNode, RetrieveResult


def _is_move_mode() -> bool:
    return settings.dicom_retrieve_mode in ("c-move", "c-move-study")


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

        Delegates the whole dance to dimsechord's SCU — register the collect
        session, drive the C-MOVE, take the arrival target from the *first*
        pending response, wait, drain, write. The counters are the reason not to
        hand-roll it: a final Success response may omit them (PS3.4 C.4.2.1.6),
        so a total summed at the end reads a stale ``num_remaining``.

        ``settings.dicom_cmove_timeout`` bounds the move and the arrival wait
        together; ``timeout`` bounds the association.

        Raises:
            RuntimeError: If the Storage SCP is not listening.
        """
        from clarinet.services.dicom.scp import get_storage_scp

        scp = get_storage_scp()
        if not scp.is_running:
            raise RuntimeError(
                f"Storage SCP not running — dicom_retrieve_mode="
                f"{settings.dicom_retrieve_mode!r} needs one to receive the "
                f"C-STORE sub-operations, and this process owns none. Either it "
                f"should (unset dicom_scp_enabled, or set it true, and have the "
                f"PACS route AET {settings.dicom_aet!r} to port "
                f"{settings.dicom_port}), or it should not retrieve via C-MOVE "
                f"(set dicom_retrieve_mode='c-get')."
            )

        config = self._create_association_config(peer.aet, peer.host, peer.port, timeout)
        request = RetrieveRequest(
            level=QueryRetrieveLevel.STUDY if series_uid is None else QueryRetrieveLevel.SERIES,
            study_instance_uid=study_uid,
            series_instance_uid=series_uid,
        )
        storage = StorageConfig(
            mode=StorageMode.MEMORY if output_dir is None else StorageMode.DISK,
            output_dir=output_dir,
        )
        return await asyncio.to_thread(
            self._operations.retrieve_via_move,
            config,
            request,
            storage,
            settings.dicom_aet,
            scp,
            settings.dicom_cmove_timeout,
            on_progress,
        )
