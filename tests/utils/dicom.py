"""Shared DICOM helpers for integration/e2e tests against a live Orthanc PACS."""

import asyncio
import contextlib
import socket
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
from dimsechord import DicomNode, RetrieveResult, StorageSCP

from clarinet.services.dicom import scp as scp_module
from clarinet.services.dicom.client import DicomClient
from clarinet.settings import settings
from tests.config import CALLING_AET, PACS_HOST, PACS_PORT, PACS_REST_PORT, PACS_REST_URL
from tests.utils.pacs_dataset import seed_pacs_dataset


def skip_unless_pacs_reachable(reason: str) -> None:
    """Skip when Orthanc is absent; fail when it is up but rejects the credentials.

    A 401/403 means the PACS answered, so the DICOM tests are runnable and only
    the test credentials are wrong. Skipping there once hid the whole DICOM
    suite behind a "not reachable" message, so it fails loudly instead.
    """
    try:
        resp = requests.get(f"{PACS_REST_URL}/system", timeout=2)
    except (requests.ConnectionError, requests.Timeout):
        pytest.skip(reason)
    if resp.status_code in (401, 403):
        pytest.fail(
            f"Orthanc at {PACS_HOST}:{PACS_REST_PORT} rejected the test REST credentials "
            f"(HTTP {resp.status_code}) — set CLARINET_TEST_PACS_REST_USER / "
            "CLARINET_TEST_PACS_REST_PASS"
        )
    if not resp.ok:
        pytest.skip(reason)


def local_ip_facing_pacs() -> str:
    """This host's address on the route to the PACS (a UDP connect sends nothing)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((PACS_HOST, PACS_PORT))
        return str(s.getsockname()[0])


def register_pacs_modality(aet: str) -> None:
    """Let ``aet`` query the test PACS.

    Stock Orthanc (``DicomAlwaysAllowFind``/``Get`` = false) answers C-FIND and
    C-GET from an unregistered AET with zero matches rather than an error, so an
    unregistered test AET looks exactly like an empty PACS. Host/port matter only
    for C-MOVE, whose tests register their own; an AET that is already known —
    under any symbolic name — is left alone because it may carry such a real
    address.
    """
    known = requests.get(f"{PACS_REST_URL}/modalities?expand", timeout=5)
    if known.ok and any(m.get("AET") == aet for m in known.json().values()):
        return
    resp = requests.put(
        f"{PACS_REST_URL}/modalities/{aet}",
        json={"AET": aet, "Host": local_ip_facing_pacs(), "Port": 11112},
        timeout=5,
    )
    if not resp.ok:
        pytest.fail(
            f"Orthanc at {PACS_HOST} refused to register modality {aet} (HTTP {resp.status_code})"
        )


def require_test_pacs(reason: str, calling_aet: str = CALLING_AET) -> None:
    """Gate for every DICOM fixture: probe Orthanc, then make it usable by the tests.

    One entry point so a new DICOM test module cannot probe the PACS and forget
    the rest: the synthetic dataset is seeded (``tests/utils/pacs_dataset.py``)
    and ``calling_aet`` is allowed to query it.
    """
    skip_unless_pacs_reachable(reason)
    seed_pacs_dataset()
    register_pacs_modality(calling_aet)


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
