"""Integration tests for schema hydration API endpoints."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from clarinet.models.record import Record, RecordStatus, RecordType
from clarinet.models.study import Series, Study
from tests.utils.factories import make_patient
from tests.utils.urls import RECORDS_BASE


@pytest_asyncio.fixture
async def _auth_override(client):
    """Ensure auth is overridden (client fixture already does this)."""
    yield


@pytest_asyncio.fixture
async def study_with_series(test_session):
    """Create a patient, study, and two series for hydration tests."""
    patient = make_patient("HYDR_PAT001", "Hydration Patient")
    test_session.add(patient)
    await test_session.flush()

    study = Study(
        patient_id=patient.id,
        study_uid="2.16.840.1.113662.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.flush()

    s1 = Series(
        study_uid=study.study_uid,
        series_uid="2.16.840.1.113662.1.1",
        series_number=1,
        series_description="Axial CT",
        modality="CT",
        instance_count=120,
    )
    s2 = Series(
        study_uid=study.study_uid,
        series_uid="2.16.840.1.113662.1.2",
        series_number=2,
        series_description="Coronal MIP",
        modality="CT",
        instance_count=30,
    )
    test_session.add_all([s1, s2])
    await test_session.commit()

    return {"patient": patient, "study": study, "series": [s1, s2]}


@pytest_asyncio.fixture
async def record_type_with_schema(test_session):
    """Create a record type with x-options in its data_schema."""
    rt = RecordType(
        name="hydration-test",
        label="Hydration Test",
        level="STUDY",
        data_schema={
            "type": "object",
            "properties": {
                "is_good": {"type": "boolean"},
                "best_series": {
                    "type": "string",
                    "x-options": {"source": "study_series"},
                },
            },
            "required": ["is_good"],
        },
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def record_type_no_schema(test_session):
    """Create a record type without data_schema."""
    rt = RecordType(
        name="no-schema-type",
        label="No Schema",
        level="STUDY",
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def record_type_static_schema(test_session):
    """Create a record type with a static schema (no x-options)."""
    rt = RecordType(
        name="static-schema-type",
        label="Static",
        level="STUDY",
        data_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        },
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def record_with_study(test_session, study_with_series, record_type_with_schema):
    """Create a record linked to a study (for hydration)."""
    data = study_with_series
    rec = Record(
        record_type_name=record_type_with_schema.name,
        patient_id=data["patient"].id,
        study_uid=data["study"].study_uid,
        status=RecordStatus.pending,
    )
    test_session.add(rec)
    await test_session.commit()
    await test_session.refresh(rec)
    return rec


@pytest.mark.asyncio
@pytest.mark.usefixtures("_auth_override")
class TestGetHydratedSchema:
    async def test_returns_hydrated_schema(self, client, record_with_study, study_with_series):
        """GET /records/{id}/schema resolves x-options to oneOf."""
        resp = await client.get(f"{RECORDS_BASE}/{record_with_study.id}/schema")
        assert resp.status_code == 200

        schema = resp.json()
        best_series = schema["properties"]["best_series"]
        assert "oneOf" in best_series
        assert "x-options" not in best_series

        uids = {opt["const"] for opt in best_series["oneOf"]}
        expected_uids = {s.series_uid for s in study_with_series["series"]}
        assert uids == expected_uids

        # Check labels contain series info
        for opt in best_series["oneOf"]:
            assert "title" in opt
            assert "#" in opt["title"]

    async def test_no_data_schema_returns_204(
        self, client, test_session, study_with_series, record_type_no_schema
    ):
        """GET /records/{id}/schema returns 204 when no data_schema."""
        data = study_with_series
        rec = Record(
            record_type_name=record_type_no_schema.name,
            patient_id=data["patient"].id,
            study_uid=data["study"].study_uid,
            status=RecordStatus.pending,
        )
        test_session.add(rec)
        await test_session.commit()
        await test_session.refresh(rec)

        resp = await client.get(f"{RECORDS_BASE}/{rec.id}/schema")
        assert resp.status_code == 204

    async def test_static_schema_returned_unchanged(
        self, client, test_session, study_with_series, record_type_static_schema
    ):
        """Schema without x-options is returned as a deep copy."""
        data = study_with_series
        rec = Record(
            record_type_name=record_type_static_schema.name,
            patient_id=data["patient"].id,
            study_uid=data["study"].study_uid,
            status=RecordStatus.pending,
        )
        test_session.add(rec)
        await test_session.commit()
        await test_session.refresh(rec)

        resp = await client.get(f"{RECORDS_BASE}/{rec.id}/schema")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["properties"]["name"] == {"type": "string"}

    async def test_patient_level_record_x_options_intact(
        self, client, test_session, record_type_with_schema
    ):
        """Patient-level record (no study_uid) → hydrator returns [], field unchanged."""
        patient = make_patient("HYDR_PAT_ONLY", "Patient Only")
        test_session.add(patient)
        await test_session.flush()

        # Override record type to patient level for this test
        rt = RecordType(
            name="patient-hydration-test",
            label="Patient Hydration",
            level="PATIENT",
            data_schema={
                "type": "object",
                "properties": {
                    "best_series": {
                        "type": "string",
                        "x-options": {"source": "study_series"},
                    },
                },
            },
        )
        test_session.add(rt)
        await test_session.flush()

        rec = Record(
            record_type_name=rt.name,
            patient_id=patient.id,
            status=RecordStatus.pending,
        )
        test_session.add(rec)
        await test_session.commit()
        await test_session.refresh(rec)

        resp = await client.get(f"{RECORDS_BASE}/{rec.id}/schema")
        assert resp.status_code == 200

        field = resp.json()["properties"]["best_series"]
        # No series → x-options stays, no oneOf
        assert "x-options" in field
        assert "oneOf" not in field


@pytest.mark.asyncio
@pytest.mark.usefixtures("_auth_override")
class TestSubmitDataWithHydratedSchema:
    async def test_submit_validates_against_hydrated_oneof(
        self, client, record_with_study, study_with_series
    ):
        """POST /records/{id}/data validates data against hydrated oneOf."""
        valid_uid = study_with_series["series"][0].series_uid
        resp = await client.post(
            f"{RECORDS_BASE}/{record_with_study.id}/data",
            json={"is_good": True, "best_series": valid_uid},
        )
        assert resp.status_code == 200

    async def test_submit_rejects_invalid_series_uid(self, client, record_with_study):
        """POST /records/{id}/data rejects a series UID not in study."""
        resp = await client.post(
            f"{RECORDS_BASE}/{record_with_study.id}/data",
            json={"is_good": True, "best_series": "9.9.9.9.9"},
        )
        assert resp.status_code == 422

    async def test_submit_without_best_series_when_not_good(self, client, record_with_study):
        """When is_good=false, best_series is not required."""
        resp = await client.post(
            f"{RECORDS_BASE}/{record_with_study.id}/data",
            json={"is_good": False},
        )
        assert resp.status_code == 200
