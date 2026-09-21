"""Regression tests for the Orthanc reachability probe.

The VM's Orthanc has REST auth on (``deploy/CLAUDE.md``). The probes used to
send no credentials and lump the resulting 401 in with connection errors, so
every DICOM test skipped as "not reachable" while the PACS was up.
"""

from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest
import requests

from tests import config
from tests.utils.dicom import (
    register_pacs_modality,
    require_test_pacs,
    skip_unless_pacs_reachable,
)


def _response(status_code: int) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = status_code < 400
    return resp


def test_rest_url_carries_credentials() -> None:
    parts = urlsplit(config.PACS_REST_URL)
    assert parts.username == config.PACS_REST_USER
    assert parts.password == config.PACS_REST_PASS
    assert parts.hostname == config.PACS_HOST
    assert parts.port == config.PACS_REST_PORT


def test_probe_passes_when_orthanc_answers() -> None:
    with patch("tests.utils.dicom.requests.get", return_value=_response(200)):
        skip_unless_pacs_reachable("unused")


@pytest.mark.parametrize("error", [requests.ConnectionError, requests.Timeout])
def test_probe_skips_when_orthanc_is_absent(error: type[Exception]) -> None:
    with (
        patch("tests.utils.dicom.requests.get", side_effect=error),
        pytest.raises(pytest.skip.Exception, match="no pacs here"),
    ):
        skip_unless_pacs_reachable("no pacs here")


@pytest.mark.parametrize("status_code", [401, 403])
def test_probe_fails_when_credentials_are_rejected(status_code: int) -> None:
    with (
        patch("tests.utils.dicom.requests.get", return_value=_response(status_code)),
        pytest.raises(pytest.fail.Exception, match="CLARINET_TEST_PACS_REST_USER") as excinfo,
    ):
        skip_unless_pacs_reachable("unused")
    # The credentialed URL must never reach test output.
    assert "@" not in str(excinfo.value)


def test_gate_seeds_the_dataset_and_registers_the_default_calling_aet() -> None:
    with (
        patch("tests.utils.dicom.requests.get", return_value=_response(200)),
        patch("tests.utils.dicom.seed_pacs_dataset") as seed,
        patch("tests.utils.dicom.register_pacs_modality") as register,
    ):
        require_test_pacs("unused")
    seed.assert_called_once_with()
    register.assert_called_once_with(config.CALLING_AET)


def test_gate_registers_a_module_specific_calling_aet() -> None:
    with (
        patch("tests.utils.dicom.requests.get", return_value=_response(200)),
        patch("tests.utils.dicom.seed_pacs_dataset"),
        patch("tests.utils.dicom.register_pacs_modality") as register,
    ):
        require_test_pacs("unused", calling_aet="SLICER_TEST")
    register.assert_called_once_with("SLICER_TEST")


def test_gate_does_not_touch_a_pacs_it_could_not_reach() -> None:
    with (
        patch("tests.utils.dicom.requests.get", side_effect=requests.ConnectionError),
        patch("tests.utils.dicom.seed_pacs_dataset") as seed,
        patch("tests.utils.dicom.register_pacs_modality") as register,
        pytest.raises(pytest.skip.Exception),
    ):
        require_test_pacs("no pacs here")
    seed.assert_not_called()
    register.assert_not_called()


def test_an_unregistered_aet_is_registered_as_a_modality() -> None:
    """Stock Orthanc answers Q/R from unknown AETs with zero matches, not an error."""
    known = _response(200)
    known.json.return_value = ["SOMEONE_ELSE"]
    with (
        patch("tests.utils.dicom.requests.get", return_value=known),
        patch("tests.utils.dicom.requests.put", return_value=_response(200)) as put,
    ):
        register_pacs_modality("CLARINET_TEST")
    assert put.call_args.args[0].endswith("/modalities/CLARINET_TEST")
    assert put.call_args.kwargs["json"]["AET"] == "CLARINET_TEST"


def test_a_registered_aet_is_left_alone() -> None:
    """It may carry a real host/port for C-MOVE — never overwrite it."""
    known = _response(200)
    known.json.return_value = ["CLARINET_TEST"]
    with (
        patch("tests.utils.dicom.requests.get", return_value=known),
        patch("tests.utils.dicom.requests.put") as put,
    ):
        register_pacs_modality("CLARINET_TEST")
    put.assert_not_called()


def test_a_refused_registration_fails_without_printing_the_credentialed_url() -> None:
    known = _response(200)
    known.json.return_value = []
    with (
        patch("tests.utils.dicom.requests.get", return_value=known),
        patch("tests.utils.dicom.requests.put", return_value=_response(403)),
        pytest.raises(pytest.fail.Exception, match="HTTP 403") as excinfo,
    ):
        register_pacs_modality("CLARINET_TEST")
    assert "@" not in str(excinfo.value)


def test_probe_skips_on_other_http_errors() -> None:
    with (
        patch("tests.utils.dicom.requests.get", return_value=_response(503)),
        pytest.raises(pytest.skip.Exception),
    ):
        skip_unless_pacs_reachable("starting up")
