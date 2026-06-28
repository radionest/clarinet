"""Unit tests for C-MOVE self-retrieval: StorageSCP, dispatch."""

import time
from unittest.mock import MagicMock, patch

import pytest

from clarinet.services.dicom.models import QueryRetrieveLevel, StorageMode
from clarinet.services.dicom.scp import StorageSCP

# ===========================================================================
# StorageSCP — session management
# ===========================================================================


class TestStorageSCPSessions:
    """Tests for StorageSCP session lifecycle (no network)."""

    def test_register_and_finish(self):
        scp = StorageSCP()
        session = scp.register_session("study1/series1")
        assert session is not None
        assert hasattr(session, "done") and hasattr(session, "instances")
        finished = scp.finish_session("study1/series1")
        assert finished is session

    def test_register_duplicate_raises(self):
        scp = StorageSCP()
        scp.register_session("study1/series1")
        with pytest.raises(RuntimeError, match="already active"):
            scp.register_session("study1/series1")
        scp.finish_session("study1/series1")

    def test_finish_nonexistent_returns_none(self):
        scp = StorageSCP()
        assert scp.finish_session("nonexistent") is None

    def test_set_expected_signals_done_if_already_received(self):
        scp = StorageSCP()
        session = scp.register_session("s/")
        # Simulate 3 instances already received
        session.received_count = 3
        scp.set_expected("s/", 3)
        assert session.done.is_set()
        scp.finish_session("s/")

    def test_set_expected_does_not_signal_if_not_enough(self):
        scp = StorageSCP()
        session = scp.register_session("s/")
        session.received_count = 1
        scp.set_expected("s/", 3)
        assert not session.done.is_set()
        scp.finish_session("s/")

    def test_wait_for_completion_returns_session(self):
        scp = StorageSCP()
        session = scp.register_session("s/")
        session.done.set()
        result = scp.wait_for_completion("s/", timeout=1.0)
        assert result is session
        scp.finish_session("s/")

    def test_wait_for_completion_timeout(self):
        scp = StorageSCP()
        scp.register_session("s/")
        start = time.monotonic()
        result = scp.wait_for_completion("s/", timeout=0.1)
        elapsed = time.monotonic() - start
        assert result is not None
        assert not result.done.is_set()
        assert elapsed < 1.0  # Didn't hang
        scp.finish_session("s/")

    def test_wait_for_completion_unknown_session(self):
        scp = StorageSCP()
        start = time.monotonic()
        result = scp.wait_for_completion("unknown", timeout=0.1)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 1.0

    def test_is_running_false_initially(self):
        scp = StorageSCP()
        assert not scp.is_running


# C-STORE handler routing is dimsechord's responsibility; end-to-end C-MOVE
# is covered by the PACS-gated integration suite (tests/e2e/).

# ===========================================================================
# StorageSCP — stop clears sessions
# ===========================================================================


class TestStorageSCPStop:
    """Tests for stop() behavior."""

    def test_stop_signals_waiting_threads(self):
        scp = StorageSCP()
        session = scp.register_session("s/")
        assert not session.done.is_set()
        scp.stop()
        # stop() should set done on all sessions so waiters don't hang
        assert session.done.is_set()
        assert not scp.is_running


# ===========================================================================
# Module-level singleton
# ===========================================================================


class TestSingleton:
    """Tests for get_storage_scp / shutdown_storage_scp."""

    def test_get_returns_same_instance(self):
        from clarinet.services.dicom.scp import get_storage_scp

        scp1 = get_storage_scp()
        scp2 = get_storage_scp()
        assert scp1 is scp2

    def test_shutdown_recreates(self):
        from clarinet.services.dicom.scp import get_storage_scp, shutdown_storage_scp

        scp1 = get_storage_scp()
        shutdown_storage_scp()
        scp2 = get_storage_scp()
        assert scp1 is not scp2


# ===========================================================================
# _retrieve() dispatch
# ===========================================================================


class TestRetrieveDispatch:
    """Tests that _retrieve() dispatches to C-GET or C-MOVE based on settings."""

    @pytest.mark.asyncio
    async def test_cget_mode_calls_get_study(self):
        from clarinet.services.dicom.client import DicomClient
        from clarinet.services.dicom.models import DicomNode

        client = DicomClient(calling_aet="TEST")
        peer = DicomNode(aet="ORTHANC", host="localhost", port=4242)

        mock_result = MagicMock()
        mock_result.num_completed = 5
        mock_result.num_failed = 0

        with (
            patch.object(client._operations, "get_study", return_value=mock_result) as mock_get,
            patch("clarinet.services.dicom.client.settings") as mock_settings,
        ):
            mock_settings.dicom_retrieve_mode = "c-get"
            result = await client._retrieve(
                study_uid="1.2.3",
                peer=peer,
                level=QueryRetrieveLevel.STUDY,
                mode=StorageMode.MEMORY,
            )
            mock_get.assert_called_once()
            assert result is mock_result

    @pytest.mark.asyncio
    async def test_cmove_mode_calls_retrieve_via_move(self):
        from clarinet.services.dicom.client import DicomClient
        from clarinet.services.dicom.models import DicomNode

        client = DicomClient(calling_aet="TEST")
        peer = DicomNode(aet="ORTHANC", host="localhost", port=4242)

        mock_result = MagicMock()
        mock_result.num_completed = 5
        mock_result.num_failed = 0

        mock_scp = MagicMock()

        with (
            patch.object(
                client._operations, "retrieve_via_move", return_value=mock_result
            ) as mock_move,
            patch("clarinet.services.dicom.client.settings") as mock_settings,
            patch("clarinet.services.dicom.scp.get_storage_scp", return_value=mock_scp),
        ):
            mock_settings.dicom_retrieve_mode = "c-move"
            mock_settings.dicom_aet = "TEST"
            mock_settings.dicom_cmove_timeout = 300.0
            result = await client._retrieve(
                study_uid="1.2.3",
                peer=peer,
                level=QueryRetrieveLevel.STUDY,
                mode=StorageMode.MEMORY,
            )
            mock_move.assert_called_once()
            assert result is mock_result
