"""Unit tests for C-MOVE self-retrieval: StorageSCP, MoveSession, dispatch."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from pydicom import Dataset

from clarinet.services.dicom.models import QueryRetrieveLevel, StorageMode
from clarinet.services.dicom.scp import MoveSession, StorageSCP

# ===========================================================================
# MoveSession
# ===========================================================================


class TestMoveSession:
    """Tests for MoveSession dataclass."""

    def test_done_event_not_set_initially(self):
        session = MoveSession()
        assert not session.done.is_set()
        assert session.received_count == 0
        assert session.expected_count is None
        assert session.instances == {}

    def test_done_event_set_when_expected_reached(self):
        session = MoveSession()
        session.expected_count = 2
        session.instances["1.2.3"] = Dataset()
        session.received_count = 1
        assert not session.done.is_set()
        session.instances["1.2.4"] = Dataset()
        session.received_count = 2
        # Simulate what SCP handler does
        if session.received_count >= session.expected_count:
            session.done.set()
        assert session.done.is_set()


# ===========================================================================
# StorageSCP — session management
# ===========================================================================


class TestStorageSCPSessions:
    """Tests for StorageSCP session lifecycle (no network)."""

    def test_register_and_finish(self):
        scp = StorageSCP()
        session = scp.register_session("study1/series1")
        assert isinstance(session, MoveSession)
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
        with scp._lock:
            session.received_count = 3
        scp.set_expected("s/", 3)
        assert session.done.is_set()
        scp.finish_session("s/")

    def test_set_expected_does_not_signal_if_not_enough(self):
        scp = StorageSCP()
        session = scp.register_session("s/")
        with scp._lock:
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


# ===========================================================================
# StorageSCP — C-STORE handler
# ===========================================================================


class TestStorageSCPHandler:
    """Tests for the _handle_store and _find_session methods."""

    def _make_event(self, study_uid: str, series_uid: str, sop_uid: str) -> MagicMock:
        """Create a mock pynetdicom C-STORE event."""
        ds = Dataset()
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.SOPInstanceUID = sop_uid
        event = MagicMock()
        event.dataset = ds
        event.file_meta = Dataset()
        return event

    def test_handle_store_deposits_to_series_session(self):
        scp = StorageSCP()
        session = scp.register_session("1.2.3/4.5.6")
        event = self._make_event("1.2.3", "4.5.6", "7.8.9")
        status = scp._handle_store(event)
        assert status == 0x0000
        assert "7.8.9" in session.instances
        assert session.received_count == 1
        scp.finish_session("1.2.3/4.5.6")

    def test_handle_store_deposits_to_study_session(self):
        scp = StorageSCP()
        session = scp.register_session("1.2.3/")
        event = self._make_event("1.2.3", "any.series", "7.8.9")
        status = scp._handle_store(event)
        assert status == 0x0000
        assert "7.8.9" in session.instances
        scp.finish_session("1.2.3/")

    def test_handle_store_series_key_preferred_over_study(self):
        scp = StorageSCP()
        study_session = scp.register_session("1.2.3/")
        series_session = scp.register_session("1.2.3/4.5.6")
        event = self._make_event("1.2.3", "4.5.6", "7.8.9")
        scp._handle_store(event)
        # Should go to series-level session, not study-level
        assert "7.8.9" in series_session.instances
        assert "7.8.9" not in study_session.instances
        scp.finish_session("1.2.3/")
        scp.finish_session("1.2.3/4.5.6")

    def test_handle_store_unregistered_accepted(self):
        """Unregistered datasets are accepted (return 0x0000) but not stored."""
        scp = StorageSCP()
        event = self._make_event("unknown", "unknown", "7.8.9")
        status = scp._handle_store(event)
        assert status == 0x0000

    def test_handle_store_signals_done_on_expected(self):
        scp = StorageSCP()
        session = scp.register_session("1.2.3/4.5.6")
        scp.set_expected("1.2.3/4.5.6", 2)
        event1 = self._make_event("1.2.3", "4.5.6", "sop1")
        event2 = self._make_event("1.2.3", "4.5.6", "sop2")
        scp._handle_store(event1)
        assert not session.done.is_set()
        scp._handle_store(event2)
        assert session.done.is_set()
        scp.finish_session("1.2.3/4.5.6")

    def test_concurrent_stores_thread_safe(self):
        """Multiple threads calling _handle_store don't corrupt session."""
        scp = StorageSCP()
        session = scp.register_session("1.2.3/")
        n = 50
        scp.set_expected("1.2.3/", n)

        def store_one(i: int):
            event = self._make_event("1.2.3", "ser", f"sop.{i}")
            scp._handle_store(event)

        threads = [threading.Thread(target=store_one, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert session.received_count == n
        assert len(session.instances) == n
        assert session.done.is_set()
        scp.finish_session("1.2.3/")


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
