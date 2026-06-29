"""Shared DICOM helpers for integration/e2e tests against a live Orthanc PACS."""

import asyncio

from clarinet.services.dicom import DicomClient, DicomNode
from clarinet.services.dicom.models import RetrieveResult
from clarinet.services.dicom.scp import StorageSCP


async def _move_series_once(
    client: DicomClient,
    scp: StorageSCP,
    study_uid: str,
    series_uid: str,
    peer: DicomNode,
    local_aet: str,
    timeout: float,  # noqa: ASYNC109 — DIMSE blocking timeout, forwarded; not asyncio cancellation
) -> RetrieveResult:
    """C-MOVE one series to the local SCP, collecting instances from the session.

    Mirrors ``AnonymizationService._move_series_to_memory`` (the production path):
    register a session, C-MOVE with our own AET as the destination, wait for the
    instances to arrive via C-STORE, then read them off the finished session.
    """
    key = f"{study_uid}/{series_uid}"
    scp.register_session(key)
    try:
        result = await client.move_series(
            study_uid=study_uid,
            series_uid=series_uid,
            peer=peer,
            destination_aet=local_aet,
            timeout=timeout,
        )
        scp.set_expected(key, result.num_completed)
        await asyncio.to_thread(scp.wait_for_completion, key, timeout)
        finished = scp.finish_session(key)
    except BaseException:
        scp.finish_session(key)
        raise
    if finished is not None:
        result.instances = finished.instances
        result.num_completed = finished.received_count
    return result


async def move_with_retry(
    client: DicomClient,
    scp: StorageSCP,
    study_uid: str,
    series_uid: str,
    peer: DicomNode,
    local_aet: str,
    *,
    timeout: float = 120.0,  # noqa: ASYNC109 — DIMSE blocking timeout, forwarded; not asyncio cancellation
    attempts: int = 3,
    backoff: float = 2.0,
) -> RetrieveResult:
    """C-MOVE a series to the local SCP, retrying the transient reverse-connection failure.

    A C-MOVE to a just-started per-test Storage SCP intermittently fails at
    association setup (zero instances received) when the PACS races to connect
    back before the SCP listener is ready. The failure is all-or-nothing at
    connection time, and each attempt registers a fresh receive session, so
    re-running is safe (no double count) and clears the transient. Only retries
    when nothing arrived — a genuinely empty retrieve still surfaces to the
    caller's assertions. Production is unaffected: the real SCP is long-lived
    (started once in the app lifespan), so this race is specific to the test
    harness's per-test SCP.
    """
    result = await _move_series_once(client, scp, study_uid, series_uid, peer, local_aet, timeout)
    for _ in range(attempts - 1):
        if result.num_completed > 0:
            break
        await asyncio.sleep(backoff)
        result = await _move_series_once(
            client, scp, study_uid, series_uid, peer, local_aet, timeout
        )
    return result
