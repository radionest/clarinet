"""Shared DICOM helpers for integration/e2e tests against a live Orthanc PACS."""

import asyncio
import contextlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from dimsechord import DicomNode, RetrieveResult, StorageSCP

from clarinet.services.dicom import scp as scp_module
from clarinet.services.dicom.client import DicomClient
from clarinet.settings import settings


@contextlib.contextmanager
def cmove_storage_scp(aet: str, port: int, ip: str = "0.0.0.0") -> Iterator[StorageSCP]:
    """Run a per-test Storage SCP as the process singleton.

    ``DicomClient`` resolves the receiving SCP through ``get_storage_scp()`` and
    sends ``settings.dicom_aet`` as the C-MOVE destination, so a test SCP has to
    replace the singleton and own the AET rather than sit beside them.
    """
    scp = StorageSCP()
    scp.start({aet: port}, ip)
    previous = scp_module._scp
    scp_module._scp = scp
    try:
        with patch.object(settings, "dicom_aet", aet):
            yield scp
    finally:
        scp_module._scp = previous
        scp.stop()


async def move_with_retry(
    client: DicomClient,
    peer: DicomNode,
    study_uid: str,
    series_uid: str | None = None,
    *,
    output_dir: Path | None = None,
    timeout: float = 120.0,  # noqa: ASYNC109 — DICOM association timeout, not asyncio
    attempts: int = 3,
    backoff: float = 2.0,
) -> RetrieveResult:
    """Retrieve via C-MOVE-to-self, retrying the transient reverse-connection failure.

    A C-MOVE to a just-started per-test Storage SCP intermittently fails at
    association setup (DICOM status 0xc000, zero instances received) when the PACS
    races to connect back before the SCP listener is ready. The failure is
    all-or-nothing at connection time, and each retrieve registers a fresh receive
    session, so re-running is safe (no double count) and clears the transient.
    Only retries when nothing arrived — a genuinely empty retrieve still surfaces
    to the caller's assertions. Production is unaffected: the real SCP is
    long-lived (started once in the app lifespan), so this race is specific to the
    test harness's per-test SCP.

    ``dicom_retrieve_mode`` is forced to c-move for the duration of the calls
    only, so a test can compare a C-MOVE against a C-GET on the same client.
    """

    async def _retrieve() -> RetrieveResult:
        with patch.object(settings, "dicom_retrieve_mode", "c-move"):
            if series_uid is None:
                if output_dir is None:
                    return await client.get_study_to_memory(study_uid, peer, timeout=timeout)
                return await client.get_study(study_uid, peer, output_dir, timeout=timeout)
            if output_dir is None:
                return await client.get_series_to_memory(
                    study_uid, series_uid, peer, timeout=timeout
                )
            return await client.get_series(study_uid, series_uid, peer, output_dir, timeout=timeout)

    result = await _retrieve()
    for _ in range(attempts - 1):
        if result.num_completed > 0:
            break
        await asyncio.sleep(backoff)
        result = await _retrieve()
    return result
