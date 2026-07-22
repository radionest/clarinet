"""D9: production script paths supply the correspondence engine.

Slicer is mocked via dependency override — asserts the ``include_correspondence``
flag on ``SlicerService.execute``; flag→payload mapping is covered by
``tests/test_slicer_build_script.py::test_build_script_with_correspondence_opt_in``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from clarinet.api.app import app
from clarinet.api.dependencies import get_slicer_service
from clarinet.models.base import DicomQueryLevel
from clarinet.models.record import Record, RecordStatus, RecordType
from clarinet.models.study import Study
from clarinet.services.slicer.service import SlicerService
from tests.utils.factories import make_patient
from tests.utils.urls import SLICER_EXEC, SLICER_RECORD_OPEN, SLICER_RECORD_VALIDATE


@pytest_asyncio.fixture
async def scripted_record(test_session) -> Record:
    """Patient + study + RecordType with both Slicer scripts + pending Record."""
    patient = make_patient("BUNDLE_PAT001", "Bundle Path Patient")
    test_session.add(patient)
    await test_session.flush()

    study = Study(
        patient_id=patient.id,
        study_uid="2.16.840.1.999.902.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.flush()

    rt = RecordType(
        name="bundle-path-test",
        level=DicomQueryLevel.STUDY,
        slicer_script="pass  # stub — execution is mocked",
        slicer_result_validator="pass  # stub — execution is mocked",
    )
    test_session.add(rt)
    await test_session.flush()

    rec = Record(
        record_type_name=rt.name,
        patient_id=patient.id,
        study_uid=study.study_uid,
        status=RecordStatus.pending,
    )
    test_session.add(rec)
    await test_session.commit()
    await test_session.refresh(rec)
    return rec


@pytest_asyncio.fixture
async def mock_slicer_service() -> AsyncGenerator[AsyncMock]:
    mock = AsyncMock(spec=SlicerService)
    mock.execute.return_value = {}

    async def _override() -> SlicerService:
        return mock  # type: ignore[return-value]

    app.dependency_overrides[get_slicer_service] = _override
    try:
        yield mock
    finally:
        app.dependency_overrides.pop(get_slicer_service, None)


def _bundle_flag(mock: AsyncMock) -> bool:
    return bool(mock.execute.call_args.kwargs.get("include_correspondence", False))


@pytest.mark.asyncio
async def test_exec_excludes_bundle_by_default(client, mock_slicer_service: AsyncMock):
    resp = await client.post(SLICER_EXEC, json={"script": "pass"})
    assert resp.status_code == 200, resp.text
    assert _bundle_flag(mock_slicer_service) is False


@pytest.mark.asyncio
async def test_exec_forwards_bundle_opt_in(client, mock_slicer_service: AsyncMock):
    resp = await client.post(SLICER_EXEC, json={"script": "pass", "include_correspondence": True})
    assert resp.status_code == 200, resp.text
    assert _bundle_flag(mock_slicer_service) is True


@pytest.mark.asyncio
async def test_open_record_includes_bundle(
    client, scripted_record: Record, mock_slicer_service: AsyncMock
):
    resp = await client.post(SLICER_RECORD_OPEN.format(record_id=scripted_record.id))
    assert resp.status_code == 200, resp.text
    assert _bundle_flag(mock_slicer_service) is True


@pytest.mark.asyncio
async def test_validate_record_includes_bundle(
    client, scripted_record: Record, mock_slicer_service: AsyncMock
):
    resp = await client.post(SLICER_RECORD_VALIDATE.format(record_id=scripted_record.id))
    assert resp.status_code == 200, resp.text
    assert _bundle_flag(mock_slicer_service) is True
