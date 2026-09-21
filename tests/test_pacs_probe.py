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
from tests.utils.dicom import skip_unless_pacs_reachable


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


def test_probe_skips_on_other_http_errors() -> None:
    with (
        patch("tests.utils.dicom.requests.get", return_value=_response(503)),
        pytest.raises(pytest.skip.Exception),
    ):
        skip_unless_pacs_reachable("starting up")
