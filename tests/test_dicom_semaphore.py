"""Tests for DICOM association semaphore in DicomOperations."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from clarinet.exceptions.http import CONFLICT
from clarinet.services.dicom.operations import DicomOperations


@pytest.fixture(autouse=True)
def _reset_semaphore():
    """Save and restore semaphore state to prevent test pollution."""
    original = DicomOperations._association_semaphore
    yield
    DicomOperations._association_semaphore = original


def test_semaphore_limits_concurrent():
    """Test that semaphore limits concurrent associations to max_concurrent."""
    # Set semaphore to allow max 2 concurrent associations
    DicomOperations.set_association_semaphore(max_concurrent=2)

    # Track concurrent usage
    current_concurrent = 0
    max_concurrent_seen = 0
    lock = threading.Lock()

    # Mock AE and association
    mock_ae = MagicMock()
    mock_assoc = MagicMock()
    mock_assoc.is_established = True
    mock_assoc.release = MagicMock()
    mock_ae.associate.return_value = mock_assoc

    # Mock config
    mock_config = MagicMock()
    mock_config.peer_host = "localhost"
    mock_config.peer_port = 11112
    mock_config.called_aet = "ORTHANC"

    def worker():
        nonlocal current_concurrent, max_concurrent_seen

        ops = DicomOperations(calling_aet="TEST")
        with ops._association(mock_ae, mock_config):
            # Track concurrent usage
            with lock:
                current_concurrent += 1
                max_concurrent_seen = max(max_concurrent_seen, current_concurrent)

            # Simulate work
            time.sleep(0.1)

            # Release counter
            with lock:
                current_concurrent -= 1

    # Launch 5 threads
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Assert that max concurrent never exceeded semaphore limit
    assert max_concurrent_seen <= 2
    assert max_concurrent_seen > 0  # Sanity check that tracking worked


def test_semaphore_released_on_failure():
    """Test that semaphore is released even when association fails."""
    # Set semaphore to 1
    DicomOperations.set_association_semaphore(max_concurrent=1)

    # Mock AE with failed association
    mock_ae = MagicMock()
    mock_assoc = MagicMock()
    mock_assoc.is_established = False  # Association fails
    mock_ae.associate.return_value = mock_assoc

    # Mock config
    mock_config = MagicMock()
    mock_config.peer_host = "localhost"
    mock_config.peer_port = 11112
    mock_config.called_aet = "ORTHANC"

    ops = DicomOperations(calling_aet="TEST")

    # Attempt association (should fail and raise CONFLICT)
    with pytest.raises(type(CONFLICT)), ops._association(mock_ae, mock_config):
        pass

    # Verify semaphore was released (value should be back to 1)
    assert DicomOperations._association_semaphore._value == 1


def test_no_semaphore_by_default():
    """Test that _association works normally when semaphore is None."""
    # Ensure semaphore is None
    DicomOperations._association_semaphore = None

    # Mock AE and association
    mock_ae = MagicMock()
    mock_assoc = MagicMock()
    mock_assoc.is_established = True
    mock_assoc.release = MagicMock()
    mock_ae.associate.return_value = mock_assoc

    # Mock config
    mock_config = MagicMock()
    mock_config.peer_host = "localhost"
    mock_config.peer_port = 11112
    mock_config.called_aet = "ORTHANC"

    ops = DicomOperations(calling_aet="TEST")

    # Should work normally without semaphore
    with ops._association(mock_ae, mock_config) as assoc:
        assert assoc.is_established

    # Verify association was released
    mock_assoc.release.assert_called_once()
