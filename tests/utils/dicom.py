"""Shared DICOM helpers for integration/e2e tests against a live Orthanc PACS."""

import time

from clarinet.services.dicom.models import (
    AssociationConfig,
    RetrieveRequest,
    RetrieveResult,
    StorageConfig,
)
from clarinet.services.dicom.operations import DicomOperations
from clarinet.services.dicom.scp import StorageSCP


def move_with_retry(
    ops: DicomOperations,
    config: AssociationConfig,
    request: RetrieveRequest,
    storage: StorageConfig,
    local_aet: str,
    scp: StorageSCP,
    *,
    timeout: float = 120.0,
    attempts: int = 3,
    backoff: float = 2.0,
) -> RetrieveResult:
    """Run a C-MOVE self-retrieval, retrying the transient reverse-connection failure.

    A C-MOVE to a just-started per-test Storage SCP intermittently fails at
    association setup (DICOM status 0xc000, zero instances received) when the PACS
    races to connect back before the SCP listener is ready. The failure is
    all-or-nothing at connection time, and ``retrieve_via_move`` registers a fresh
    receive session per call, so re-running is safe (no double count) and clears
    the transient. Only retries when nothing arrived — a genuinely empty retrieve
    still surfaces to the caller's assertions. Production is unaffected: the real
    SCP is long-lived (started once in the app lifespan), so this race is specific
    to the test harness's per-test SCP. Call inside ``asyncio.to_thread`` — it
    blocks on ``time.sleep`` between attempts.
    """
    result = ops.retrieve_via_move(config, request, storage, local_aet, scp, timeout=timeout)
    for _ in range(attempts - 1):
        if result.num_completed > 0:
            break
        time.sleep(backoff)
        result = ops.retrieve_via_move(config, request, storage, local_aet, scp, timeout=timeout)
    return result
