"""Unit tests for the Storage SCP singleton and c-move retrieve dispatch.

Move-to-self itself is dimsechord's ``DicomOperations.retrieve_via_move`` and is
tested there. What Clarinet owns, and what these cover, is: which process gets a
listener, the ``dicom_retrieve_mode`` dispatch, and the translation of a
Clarinet-level call into the request/storage/AET that dimsechord is handed.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dimsechord import DicomClient as DimsechordClient
from dimsechord import QueryRetrieveLevel
from dimsechord._models import StorageMode

from clarinet.services.dicom.client import DicomClient
from clarinet.services.dicom.models import DicomNode

PEER = DicomNode(aet="ORTHANC", host="localhost", port=4242)


@pytest.fixture(autouse=True)
def _reset_scp_singleton():
    """Never leak a patched singleton into the rest of the xdist worker."""
    from clarinet.services.dicom import scp as scp_module

    yield
    scp_module.shutdown_storage_scp()


def _scp(*, running: bool = True) -> MagicMock:
    scp = MagicMock()
    scp.is_running = running
    return scp


#: Deliberately not the 300.0 that ``get_*`` defaults ``timeout`` to — the two
#: budgets are different things and a shared value cannot tell them apart.
ARRIVAL_BUDGET = 123.0


def _move_settings(settings: MagicMock, mode: str = "c-move") -> None:
    settings.dicom_retrieve_mode = mode
    settings.dicom_aet = "CLARINET"
    settings.dicom_port = 11112
    settings.dicom_cmove_timeout = ARRIVAL_BUDGET


class TestOwnership:
    """Which process gets to own the C-MOVE listener."""

    @pytest.mark.parametrize(
        ("mode", "wanted"),
        [
            ("c-move", True),
            ("c-move-study", True),
            ("c-get", False),
            ("c-get-study", False),
        ],
    )
    def test_mode_implies_ownership(self, mode: str, wanted: bool):
        from clarinet.services.dicom import scp as scp_module

        with patch.object(scp_module, "settings") as settings:
            settings.dicom_scp_enabled = None
            settings.dicom_retrieve_mode = mode
            assert scp_module.storage_scp_wanted() is wanted

    @pytest.mark.parametrize("enabled", [True, False])
    def test_explicit_setting_overrides_the_mode(self, enabled: bool):
        """A second process on the host opts out even though the mode is c-move."""
        from clarinet.services.dicom import scp as scp_module

        with patch.object(scp_module, "settings") as settings:
            settings.dicom_scp_enabled = enabled
            settings.dicom_retrieve_mode = "c-move"
            assert scp_module.storage_scp_wanted() is enabled

    def test_bind_collision_names_the_port_and_the_ways_out(self):
        """The operator must not have to guess which process took the port."""
        from clarinet.services.dicom import scp as scp_module

        scp_module.shutdown_storage_scp()
        instance = MagicMock()
        instance.is_running = False
        instance.start.side_effect = OSError("[Errno 98] Address already in use")
        with (
            patch.object(scp_module, "StorageSCP", return_value=instance),
            patch.object(scp_module, "settings") as settings,
        ):
            settings.dicom_aet = "CLARINET"
            settings.dicom_port = 11112
            settings.dicom_ip = None
            with pytest.raises(OSError) as excinfo:
                scp_module.start_storage_scp()
        message = str(excinfo.value)
        assert "11112" in message
        assert "CLARINET" in message
        assert "--dicom AET:PORT" in message
        assert "dicom_scp_enabled=false" in message
        scp_module.shutdown_storage_scp()


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
        expected = MagicMock()

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
        scp = _scp()

        with (
            patch.object(DimsechordClient, "get_series", new=AsyncMock()) as cget,
            patch.object(client._operations, "retrieve_via_move") as move,
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=scp),
        ):
            _move_settings(settings)
            await client.get_series("1.2.3", "1.2.4", PEER, tmp_path)

        cget.assert_not_awaited()
        move.assert_called_once()

    async def test_cmove_without_running_scp_names_the_ways_out(self, tmp_path: Path):
        client = DicomClient(calling_aet="TEST")

        with (
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=_scp(running=False)),
        ):
            _move_settings(settings)
            with pytest.raises(RuntimeError) as excinfo:
                await client.get_series("1.2.3", "1.2.4", PEER, tmp_path)

        message = str(excinfo.value)
        assert "dicom_scp_enabled" in message
        assert "dicom_retrieve_mode='c-get'" in message
        assert "CLARINET" in message


class TestMoveDelegation:
    """What Clarinet hands dimsechord's retrieve_via_move."""

    async def _call(self, client: DicomClient, scp: MagicMock, **kwargs) -> MagicMock:
        with (
            patch.object(client._operations, "retrieve_via_move") as move,
            patch("clarinet.services.dicom.client.settings") as settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=scp),
        ):
            _move_settings(settings, kwargs.pop("mode", "c-move"))
            await kwargs.pop("call")()
        return move

    async def test_series_call_becomes_a_series_level_memory_retrieve(self):
        client = DicomClient(calling_aet="TEST")
        scp = _scp()
        move = await self._call(
            client,
            scp,
            call=lambda: client.get_series_to_memory("1.2.3", "1.2.4", PEER),
        )

        config, request, storage, local_aet, passed_scp, budget, _ = move.call_args.args
        assert request.level is QueryRetrieveLevel.SERIES
        assert request.study_instance_uid == "1.2.3"
        assert request.series_instance_uid == "1.2.4"
        assert storage.mode is StorageMode.MEMORY
        assert storage.output_dir is None
        assert (config.called_aet, config.peer_host, config.peer_port) == (
            PEER.aet,
            PEER.host,
            PEER.port,
        )
        assert local_aet == "CLARINET", "the C-MOVE destination is us, not the peer"
        assert passed_scp is scp
        # Both budgets asserted: swapping them would otherwise pass.
        assert budget == ARRIVAL_BUDGET, "arrival budget is dicom_cmove_timeout"
        assert config.timeout == 300.0, "association timeout is the method's own"

    async def test_study_call_to_disk_becomes_a_study_level_disk_retrieve(self, tmp_path: Path):
        client = DicomClient(calling_aet="TEST")
        move = await self._call(
            client,
            _scp(),
            mode="c-move-study",
            call=lambda: client.get_study("1.2.3", PEER, tmp_path / "out"),
        )

        _, request, storage, _, _, _, _ = move.call_args.args
        assert request.level is QueryRetrieveLevel.STUDY
        assert request.series_instance_uid is None
        assert storage.mode is StorageMode.DISK
        assert storage.output_dir == tmp_path / "out"

    async def test_progress_callback_is_forwarded(self):
        client = DicomClient(calling_aet="TEST")

        def on_progress(received: int, total: int | None) -> None:  # pragma: no cover
            pass

        move = await self._call(
            client,
            _scp(),
            call=lambda: client.get_study_to_memory("1.2.3", PEER, on_progress=on_progress),
        )

        assert move.call_args.args[-1] is on_progress

    async def test_level_follows_the_call_not_the_mode_suffix(self):
        """``c-move-study`` retrieving one series is still a SERIES-level move.

        The suffix is the Slicer helper's batching hint; it must not widen a
        series request into a whole study.
        """
        client = DicomClient(calling_aet="TEST")
        move = await self._call(
            client,
            _scp(),
            mode="c-move-study",
            call=lambda: client.get_series_to_memory("1.2.3", "1.2.4", PEER),
        )

        request = move.call_args.args[1]
        assert request.level is QueryRetrieveLevel.SERIES
        assert request.series_instance_uid == "1.2.4"
