"""Unit tests for AnonymizationOrchestrator."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clarinet.client import ClarinetAPIError
from clarinet.exceptions.domain import AnonymizationFailedError
from clarinet.models.base import RecordStatus
from clarinet.services.dicom.models import AnonymizationResult
from clarinet.services.dicom.orchestrator import AnonymizationOrchestrator


def _result(**overrides) -> AnonymizationResult:
    """Build a successful AnonymizationResult with sensible defaults."""
    base = {
        "study_uid": "1.2.3",
        "anon_study_uid": "9.9.9",
        "series_count": 1,
        "series_anonymized": 1,
        "series_skipped": 0,
        "instances_anonymized": 5,
        "instances_failed": 0,
        "instances_send_failed": 0,
        "sent_to_pacs": False,
    }
    base.update(overrides)
    return AnonymizationResult(**base)


def _orchestrator(
    *,
    study=None,
    record=None,
    anon_result=None,
    anon_error=None,
    patient_anon_error=None,
):
    """Build an Orchestrator with mocked client and service.

    Returns: (orchestrator, client_mock, service_mock).
    """
    client = MagicMock()
    client.get_study = AsyncMock(
        return_value=study or SimpleNamespace(anon_uid=None, patient_id="P1")
    )
    client.get_record = AsyncMock(
        return_value=record or SimpleNamespace(status=RecordStatus.pending, data=None)
    )
    client.anonymize_patient = AsyncMock()
    if patient_anon_error is not None:
        client.anonymize_patient.side_effect = patient_anon_error
    client.submit_record_data = AsyncMock()
    client.update_record_data = AsyncMock()
    client.update_record_status = AsyncMock()

    service = MagicMock()
    if anon_error is not None:
        service.anonymize_study = AsyncMock(side_effect=anon_error)
    else:
        service.anonymize_study = AsyncMock(return_value=anon_result or _result())

    return AnonymizationOrchestrator(service, client), client, service


@pytest.mark.asyncio
async def test_run_without_record_id_skips_bookkeeping() -> None:
    """No record_id: only ensures Patient and runs anonymization."""
    orch, client, service = _orchestrator()

    result = await orch.run("1.2.3", record_id=None, save_to_disk=False, send_to_pacs=False)

    client.anonymize_patient.assert_awaited_once_with("P1")
    service.anonymize_study.assert_awaited_once_with(
        "1.2.3", save_to_disk=False, send_to_pacs=False, per_study_patient_id=None
    )
    client.submit_record_data.assert_not_awaited()
    client.update_record_data.assert_not_awaited()
    assert result.anon_study_uid == "9.9.9"


@pytest.mark.asyncio
async def test_skip_guard_returns_existing_anon_uid() -> None:
    """Already done: study has anon_uid, no error, sent_to_pacs matches."""
    study = SimpleNamespace(anon_uid="EXISTING", patient_id="P1")
    record = SimpleNamespace(status=RecordStatus.finished, data={"sent_to_pacs": True})
    orch, client, service = _orchestrator(study=study, record=record)

    result = await orch.run("1.2.3", record_id=42, send_to_pacs=True)

    service.anonymize_study.assert_not_awaited()
    client.anonymize_patient.assert_not_awaited()
    # Re-submit on a finished record uses PATCH (update_record_data).
    client.update_record_data.assert_awaited_once()
    args = client.update_record_data.await_args
    assert args.args[0] == 42
    assert args.args[1]["skipped"] is True
    assert args.args[1]["anon_study_uid"] == "EXISTING"
    assert result.anon_study_uid == "EXISTING"


@pytest.mark.asyncio
async def test_skip_guard_bypassed_when_prev_error() -> None:
    """anon_uid present but prev attempt errored → re-run."""
    study = SimpleNamespace(anon_uid="EXISTING", patient_id="P1")
    record = SimpleNamespace(
        status=RecordStatus.failed, data={"error": "boom", "sent_to_pacs": True}
    )
    orch, client, service = _orchestrator(study=study, record=record)

    await orch.run("1.2.3", record_id=42, send_to_pacs=True)

    service.anonymize_study.assert_awaited_once()
    client.submit_record_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_skip_guard_bypassed_when_send_to_pacs_pending() -> None:
    """anon_uid present but send_to_pacs=True now and prev didn't send → re-run."""
    study = SimpleNamespace(anon_uid="EXISTING", patient_id="P1")
    record = SimpleNamespace(status=RecordStatus.finished, data={"sent_to_pacs": False})
    orch, _client, service = _orchestrator(study=study, record=record)

    await orch.run("1.2.3", record_id=42, send_to_pacs=True)

    service.anonymize_study.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_path_submits_full_result() -> None:
    """Success: AnonymizationResult fields are written to the Record."""
    record = SimpleNamespace(status=RecordStatus.pending, data=None)
    orch, client, _service = _orchestrator(
        record=record,
        anon_result=_result(
            anon_study_uid="ANON",
            instances_anonymized=10,
            instances_failed=2,
            instances_send_failed=1,
            sent_to_pacs=True,
            series_count=3,
            series_anonymized=3,
            series_skipped=0,
        ),
    )

    await orch.run("1.2.3", record_id=42)

    client.submit_record_data.assert_awaited_once()
    args = client.submit_record_data.await_args
    assert args.args[0] == 42
    data = args.args[1]
    assert data["anon_study_uid"] == "ANON"
    assert data["instances_anonymized"] == 10
    assert data["instances_failed"] == 2
    assert data["instances_send_failed"] == 1
    assert data["sent_to_pacs"] is True
    assert data["series_count"] == 3
    assert "skipped" not in data


@pytest.mark.asyncio
async def test_error_path_submits_failed_status_and_reraises() -> None:
    """AnonymizationFailedError → submit error+failed, re-raise."""
    record = SimpleNamespace(status=RecordStatus.pending, data=None)
    orch, client, _ = _orchestrator(
        record=record, anon_error=AnonymizationFailedError("threshold exceeded")
    )

    with pytest.raises(AnonymizationFailedError):
        await orch.run("1.2.3", record_id=42)

    client.submit_record_data.assert_awaited_once()
    args = client.submit_record_data.await_args
    assert "threshold exceeded" in args.args[1]["error"]
    assert args.kwargs["status"] == RecordStatus.failed


@pytest.mark.asyncio
async def test_general_exception_marks_record_failed_and_reraises() -> None:
    """Any non-domain exception (network, runtime) also marks the Record failed."""
    record = SimpleNamespace(status=RecordStatus.pending, data=None)
    orch, client, _ = _orchestrator(record=record, anon_error=RuntimeError("PACS connection lost"))

    with pytest.raises(RuntimeError):
        await orch.run("1.2.3", record_id=42)

    client.submit_record_data.assert_awaited_once()
    args = client.submit_record_data.await_args
    assert "PACS connection lost" in args.args[1]["error"]
    assert args.kwargs["status"] == RecordStatus.failed


@pytest.mark.asyncio
async def test_patient_already_anonymized_409_is_ignored() -> None:
    """409 from anonymize_patient is benign and does not abort the flow."""
    orch, client, service = _orchestrator(
        patient_anon_error=ClarinetAPIError("already", status_code=409)
    )

    await orch.run("1.2.3", record_id=42)

    client.anonymize_patient.assert_awaited_once()
    service.anonymize_study.assert_awaited_once()
    client.submit_record_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_patient_anonymize_non_409_propagates() -> None:
    """Non-409 errors from anonymize_patient propagate."""
    orch, _, service = _orchestrator(patient_anon_error=ClarinetAPIError("server", status_code=500))

    with pytest.raises(ClarinetAPIError):
        await orch.run("1.2.3", record_id=42)

    service.anonymize_study.assert_not_awaited()


@pytest.mark.asyncio
async def test_extra_record_data_merges_into_success() -> None:
    """extra_record_data is merged into the submitted dict."""
    record = SimpleNamespace(status=RecordStatus.pending, data=None)
    orch, client, _ = _orchestrator(record=record)

    await orch.run(
        "1.2.3",
        record_id=42,
        extra_record_data={"study_type": "CT", "extra": 1},
    )

    args = client.submit_record_data.await_args
    assert args.args[1]["study_type"] == "CT"
    assert args.args[1]["extra"] == 1
    assert args.args[1]["anon_study_uid"] == "9.9.9"


@pytest.mark.asyncio
async def test_extra_record_data_merges_into_skip_branch() -> None:
    """extra_record_data is included in the skip-guard submission."""
    study = SimpleNamespace(anon_uid="EXISTING", patient_id="P1")
    record = SimpleNamespace(status=RecordStatus.pending, data={"sent_to_pacs": True})
    orch, client, _ = _orchestrator(study=study, record=record)

    await orch.run(
        "1.2.3",
        record_id=42,
        send_to_pacs=True,
        extra_record_data={"study_type": "MRI"},
    )

    args = client.submit_record_data.await_args
    assert args.args[1]["skipped"] is True
    assert args.args[1]["study_type"] == "MRI"


@pytest.mark.asyncio
async def test_extra_record_data_merges_into_error_branch() -> None:
    """extra_record_data is included when submitting the error."""
    record = SimpleNamespace(status=RecordStatus.pending, data=None)
    orch, client, _ = _orchestrator(record=record, anon_error=AnonymizationFailedError("x"))

    with pytest.raises(AnonymizationFailedError):
        await orch.run("1.2.3", record_id=42, extra_record_data={"study_type": "CT"})

    args = client.submit_record_data.await_args
    assert args.args[1]["study_type"] == "CT"
    assert "error" in args.args[1]


@pytest.mark.asyncio
async def test_finished_record_uses_patch_for_resubmit() -> None:
    """A finished record gets PATCH (update_record_data), not POST."""
    record = SimpleNamespace(status=RecordStatus.finished, data={"old": True})
    orch, client, _ = _orchestrator(record=record)

    await orch.run("1.2.3", record_id=42)

    client.update_record_data.assert_awaited_once()
    client.submit_record_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_record_uses_post_for_submit() -> None:
    """A pending record gets POST (submit_record_data)."""
    record = SimpleNamespace(status=RecordStatus.pending, data=None)
    orch, client, _ = _orchestrator(record=record)

    await orch.run("1.2.3", record_id=42)

    client.submit_record_data.assert_awaited_once()
    client.update_record_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_status_on_finished_record_uses_patch_then_status() -> None:
    """status=failed on a finished Record uses PATCH + update_record_status.

    POST submit_record_data on a finished Record returns 409, which would
    swallow the original anonymization error. Use PATCH for data, then
    update_record_status to perform the status transition.
    """
    record = SimpleNamespace(status=RecordStatus.finished, data=None)
    orch, client, _ = _orchestrator(record=record, anon_error=AnonymizationFailedError("x"))

    with pytest.raises(AnonymizationFailedError):
        await orch.run("1.2.3", record_id=42)

    client.submit_record_data.assert_not_awaited()
    client.update_record_data.assert_awaited_once()
    client.update_record_status.assert_awaited_once_with(42, RecordStatus.failed)


@pytest.mark.asyncio
async def test_preflight_get_study_failure_marks_record_failed() -> None:
    """Pre-flight get_study failure also writes error to the Record."""
    record = SimpleNamespace(status=RecordStatus.pending, data=None)
    orch, client, _service = _orchestrator(record=record)
    client.get_study = AsyncMock(side_effect=RuntimeError("network down"))

    with pytest.raises(RuntimeError):
        await orch.run("1.2.3", record_id=42)

    client.submit_record_data.assert_awaited_once()
    args = client.submit_record_data.await_args
    assert "network down" in args.args[1]["error"]
    assert args.kwargs["status"] == RecordStatus.failed


@pytest.mark.asyncio
async def test_preflight_patient_anonymize_failure_marks_record_failed() -> None:
    """Non-409 error from anonymize_patient also marks Record failed."""
    record = SimpleNamespace(status=RecordStatus.pending, data=None)
    orch, client, _service = _orchestrator(
        record=record, patient_anon_error=ClarinetAPIError("server", status_code=500)
    )

    with pytest.raises(ClarinetAPIError):
        await orch.run("1.2.3", record_id=42)

    client.submit_record_data.assert_awaited_once()
    assert client.submit_record_data.await_args.kwargs["status"] == RecordStatus.failed


@pytest.mark.asyncio
async def test_resolved_send_to_pacs_passed_to_anon_service() -> None:
    """settings defaults reach anon_service.anonymize_study (consistency)."""
    record = SimpleNamespace(status=RecordStatus.pending, data=None)
    orch, _client, service = _orchestrator(record=record)

    with patch("clarinet.services.dicom.orchestrator.settings") as mock_settings:
        mock_settings.anon_save_to_disk = True
        mock_settings.anon_send_to_pacs = True
        await orch.run("1.2.3", record_id=42)

    service.anonymize_study.assert_awaited_once_with(
        "1.2.3", save_to_disk=True, send_to_pacs=True, per_study_patient_id=None
    )
