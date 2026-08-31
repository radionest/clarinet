"""Unit tests for the Storage SCP singleton and c-move retrieve dispatch.

The SCP itself is dimsechord's and tested there; what Clarinet owns is the
singleton lifecycle and the ``dicom_retrieve_mode`` dispatch — including the
move-to-self path, which is the only way to retrieve from a peer that offers
C-MOVE but no C-GET.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dimsechord import DicomClient as DimsechordClient

from clarinet.services.dicom.client import DicomClient
from clarinet.services.dicom.models import DicomNode

PEER = DicomNode(aet="ORTHANC", host="localhost", port=4242)


def _move_result(num_completed: int = 2) -> MagicMock:
    result = MagicMock()
    result.status = "success"
    result.num_completed = num_completed
    result.num_failed = 0
    result.instances = {}
    return result


def _session(received: int = 2, expected: int | None = 2) -> MagicMock:
    session = MagicMock()
    session.instances = {f"1.2.{i}": MagicMock() for i in range(received)}
    session.received_count = received
    session.expected_count = expected
    return session


def _scp(session: MagicMock, *, running: bool = True, arrived: bool = True) -> MagicMock:
    scp = MagicMock()
    scp.is_running = running
    scp.register_session.return_value = session
    scp.wait_for_completion.return_value = arrived
    scp.finish_session.return_value = session
    return scp


class TestSingleton:
    """Tests for get_storage_scp / start_storage_scp / shutdown_storage_scp."""

    def test_get_returns_same_instance(self):
        from clarinet.services.dicom.scp import get_storage_scp

        assert get_storage_scp() is get_storage_scp()

    def test_shutdown_recreates(self):
        from clarinet.services.dicom.scp import get_storage_scp, shutdown_storage_scp

        scp1 = get_storage_scp()
        shutdown_storage_scp()
        assert get_storage_scp() is not scp1

    def test_start_binds_configured_aet_and_port(self):
        from clarinet.services.dicom import scp as scp_module

        scp_module.shutdown_storage_scp()
        instance = MagicMock()
        instance.is_running = False
        with (
            patch.object(scp_module, "StorageSCP", return_value=instance),
            patch.object(scp_module, "settings") as settings,
        ):
            settings.dicom_aet = "CLARINET"
            settings.dicom_port = 11112
            settings.dicom_ip = None
            scp_module.start_storage_scp()
        instance.start.assert_called_once_with({"CLARINET": 11112}, "0.0.0.0")
        scp_module.shutdown_storage_scp()

    def test_start_is_noop_when_already_running(self):
        from clarinet.services.dicom import scp as scp_module

        scp_module.shutdown_storage_scp()
        instance = MagicMock()
        instance.is_running = True
        with patch.object(scp_module, "StorageSCP", return_value=instance):
            scp_module.start_storage_scp()
        instance.start.assert_not_called()
        scp_module.shutdown_storage_scp()


class TestRetrieveDispatch:
    """``dicom_retrieve_mode``, not the call site, picks the transport."""

    async def test_cget_mode_uses_dimsechord_cget(self, tmp_path: Path):
        client = DicomClient(calling_aet="TEST")
        expected = _move_result()

        with (
            patch.object(
                DimsechordClient, "get_series", new=AsyncMock(return_value=expected)
            ) as cget,
            patch("clarinet.services.dicom.client.settings") as settings,
        ):
            settings.dicom_retrieve_mode = "c-get"
            result = await client.get_series("1.2.3", "1.2.4", PEER, tmp_path)

        cget.assert_awaited_once()
        assert result is expected

    async def test_cmove_mode_moves_to_self_instead_of_cget(self, tmp_path: Path):
        """The regression guard: c-move must never silently fall back to C-GET.

        A peer that offers C-MOVE but no C-GET rejects the C-GET presentation
        context outright, so a fallback is an outage, not a degradation.
        """
        client = DicomClient(calling_aet="TEST")
        scp = _scp(_session())

        with (
            patch.object(DimsechordClient, "get_series", new=AsyncMock()) as cget,
            patch.object(
                DimsechordClient, "move_series", new=AsyncMock(return_value=_move_result())
            ) as move,
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=scp),
        ):
            settings.dicom_retrieve_mode = "c-move"
            settings.dicom_aet = "CLARINET"
            settings.dicom_cmove_timeout = 300.0
            await client.get_series("1.2.3", "1.2.4", PEER, tmp_path)

        cget.assert_not_awaited()
        move.assert_awaited_once_with("1.2.3", "1.2.4", PEER, "CLARINET", timeout=300.0)

    async def test_cmove_study_mode_moves_the_study(self, tmp_path: Path):
        client = DicomClient(calling_aet="TEST")
        scp = _scp(_session())

        with (
            patch.object(
                DimsechordClient, "move_study", new=AsyncMock(return_value=_move_result())
            ) as move,
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=scp),
        ):
            settings.dicom_retrieve_mode = "c-move-study"
            settings.dicom_aet = "CLARINET"
            settings.dicom_cmove_timeout = 300.0
            await client.get_study("1.2.3", PEER, tmp_path)

        move.assert_awaited_once_with("1.2.3", PEER, "CLARINET", timeout=300.0)

    async def test_cmove_without_running_scp_raises(self, tmp_path: Path):
        client = DicomClient(calling_aet="TEST")
        scp = _scp(_session(), running=False)

        with (
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=scp),
        ):
            settings.dicom_retrieve_mode = "c-move"
            with pytest.raises(RuntimeError, match="Storage SCP not running"):
                await client.get_series("1.2.3", "1.2.4", PEER, tmp_path)


class TestRetrieveViaMove:
    """What physically arrived on the SCP, not the peer's tally, is the result."""

    async def test_result_reflects_arrivals_not_move_counts(self):
        client = DicomClient(calling_aet="TEST")
        session = _session(received=2)
        scp = _scp(session)

        with (
            patch.object(
                DimsechordClient,
                "move_series",
                new=AsyncMock(return_value=_move_result(num_completed=5)),
            ),
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=scp),
        ):
            settings.dicom_retrieve_mode = "c-move"
            settings.dicom_aet = "CLARINET"
            settings.dicom_cmove_timeout = 300.0
            result = await client.get_series_to_memory("1.2.3", "1.2.4", PEER)

        scp.register_session.assert_called_once_with("1.2.3/1.2.4", collect=True)
        scp.set_expected.assert_called_once_with("1.2.3/1.2.4", 5)
        assert result.num_completed == 2
        assert result.instances is session.instances

    async def test_writes_instances_when_output_dir_given(self, tmp_path: Path):
        client = DicomClient(calling_aet="TEST")
        session = _session(received=0)
        session.instances = {"1.2.9": MagicMock()}
        scp = _scp(session)

        with (
            patch.object(
                DimsechordClient, "move_series", new=AsyncMock(return_value=_move_result())
            ),
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=scp),
        ):
            settings.dicom_retrieve_mode = "c-move"
            settings.dicom_aet = "CLARINET"
            settings.dicom_cmove_timeout = 300.0
            await client.get_series("1.2.3", "1.2.4", PEER, tmp_path / "out")

        session.instances["1.2.9"].save_as.assert_called_once_with(
            tmp_path / "out" / "1.2.9.dcm", enforce_file_format=True
        )

    async def test_shortfall_marks_result_timeout(self):
        client = DicomClient(calling_aet="TEST")
        scp = _scp(_session(received=1, expected=5), arrived=False)

        with (
            patch.object(
                DimsechordClient,
                "move_series",
                new=AsyncMock(return_value=_move_result(num_completed=5)),
            ),
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=scp),
        ):
            settings.dicom_retrieve_mode = "c-move"
            settings.dicom_aet = "CLARINET"
            settings.dicom_cmove_timeout = 300.0
            result = await client.get_series_to_memory("1.2.3", "1.2.4", PEER)

        assert result.status == "timeout"

    async def test_session_is_finished_when_move_raises(self):
        client = DicomClient(calling_aet="TEST")
        scp = _scp(_session())

        with (
            patch.object(
                DimsechordClient, "move_series", new=AsyncMock(side_effect=OSError("boom"))
            ),
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=scp),
        ):
            settings.dicom_retrieve_mode = "c-move"
            settings.dicom_aet = "CLARINET"
            settings.dicom_cmove_timeout = 300.0
            with pytest.raises(OSError, match="boom"):
                await client.get_series_to_memory("1.2.3", "1.2.4", PEER)

        scp.finish_session.assert_called_with("1.2.3/1.2.4")
