"""
Comprehensive functionality test for the Clarinet demo project.

Tests all core API features: auth, patients, studies, series,
record types, records, advanced search, hierarchy, batch ops, and RecordFlow.

Usage:
    cd examples/demo
    python scripts/test_functionality.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path so we can import src
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.client import ClarinetClient, ClarinetAPIError
from src.models.base import RecordStatus

BASE_URL = "http://localhost:8000/api"

passed = 0
failed = 0
errors: list[str] = []


def ok(name: str) -> None:
    global passed
    passed += 1
    print(f"  [PASS] {name}")


def fail(name: str, reason: str = "") -> None:
    global failed
    msg = f"  [FAIL] {name}" + (f" -- {reason}" if reason else "")
    failed += 1
    errors.append(msg)
    print(msg)


async def test_auth(client: ClarinetClient) -> None:
    """Test authentication endpoints."""
    print("\n--- 1. Authentication ---")

    # get_me
    try:
        me = await client.get_me()
        assert me.email is not None
        ok("get_me")
    except Exception as e:
        fail("get_me", str(e))

    # validate_session
    try:
        user = await client.validate_session()
        assert user.email is not None
        ok("validate_session")
    except Exception as e:
        fail("validate_session", str(e))

    # logout + re-login
    try:
        await client.logout()
        await client.login("admin", "admin123")
        me2 = await client.get_me()
        assert me2.email is not None
        ok("logout + re-login")
    except Exception as e:
        fail("logout + re-login", str(e))


async def test_patients(client: ClarinetClient) -> None:
    """Test patient management."""
    print("\n--- 2. Patients ---")

    # create
    try:
        patient = await client.create_patient({
            "patient_id": "TEST_P001",
            "patient_name": "Тестов Тест Тестович",
        })
        assert patient.id == "TEST_P001"
        ok("create_patient")
    except ClarinetAPIError:
        # May already exist from previous run
        ok("create_patient (already exists)")
    except Exception as e:
        fail("create_patient", str(e))

    # get_all
    try:
        patients = await client.get_patients()
        assert len(patients) > 0
        ok(f"get_patients ({len(patients)} found)")
    except Exception as e:
        fail("get_patients", str(e))

    # get_by_id
    try:
        p = await client.get_patient("TEST_P001")
        assert p.id == "TEST_P001"
        ok("get_patient by id")
    except Exception as e:
        fail("get_patient by id", str(e))

    # anonymize
    try:
        anon = await client.anonymize_patient("TEST_P001")
        ok(f"anonymize_patient (anon_id={anon.anon_id})")
    except Exception as e:
        fail("anonymize_patient", str(e))


async def test_studies(client: ClarinetClient) -> None:
    """Test study management."""
    print("\n--- 3. Studies ---")

    # create
    try:
        study = await client.create_study({
            "study_uid": "1.2.840.99999.1",
            "date": "2024-06-15",
            "patient_id": "TEST_P001",
        })
        assert study.study_uid == "1.2.840.99999.1"
        ok("create_study")
    except ClarinetAPIError:
        ok("create_study (already exists)")
    except Exception as e:
        fail("create_study", str(e))

    # get_all
    try:
        studies = await client.get_studies()
        assert len(studies) > 0
        ok(f"get_studies ({len(studies)} found)")
    except Exception as e:
        fail("get_studies", str(e))

    # get_by_uid
    try:
        s = await client.get_study("1.2.840.99999.1")
        assert s.study_uid == "1.2.840.99999.1"
        ok("get_study by uid")
    except Exception as e:
        fail("get_study by uid", str(e))

    # get_series
    try:
        series = await client.get_study_series("1.2.840.99999.1")
        ok(f"get_study_series ({len(series)} series)")
    except Exception as e:
        fail("get_study_series", str(e))

    # add_anonymized_uid
    try:
        s = await client.add_anonymized_study_uid("1.2.840.99999.1", "1.2.840.99999.1.ANON")
        assert s.anon_uid == "1.2.840.99999.1.ANON"
        ok("add_anonymized_study_uid")
    except Exception as e:
        fail("add_anonymized_study_uid", str(e))


async def test_series(client: ClarinetClient) -> None:
    """Test series management."""
    print("\n--- 4. Series ---")

    # create
    try:
        series = await client.create_series({
            "series_uid": "1.2.840.99999.1.1",
            "series_number": 1,
            "study_uid": "1.2.840.99999.1",
            "series_description": "T1 test",
        })
        assert series.series_uid == "1.2.840.99999.1.1"
        ok("create_series")
    except ClarinetAPIError:
        ok("create_series (already exists)")
    except Exception as e:
        fail("create_series", str(e))

    # get_all
    try:
        all_s = await client.get_all_series()
        assert len(all_s) > 0
        ok(f"get_all_series ({len(all_s)} found)")
    except Exception as e:
        fail("get_all_series", str(e))

    # get_by_uid
    try:
        s = await client.get_series("1.2.840.99999.1.1")
        assert s.series_uid == "1.2.840.99999.1.1"
        ok("get_series by uid")
    except Exception as e:
        fail("get_series by uid", str(e))

    # find
    try:
        found = await client.find_series({"study_uid": "1.2.840.99999.1"})
        assert len(found) > 0
        ok(f"find_series ({len(found)} found)")
    except Exception as e:
        fail("find_series", str(e))

    # add_anonymized_uid
    try:
        s = await client.add_anonymized_series_uid("1.2.840.99999.1.1", "1.2.840.99999.1.1.ANON")
        assert s.anon_uid == "1.2.840.99999.1.1.ANON"
        ok("add_anonymized_series_uid")
    except Exception as e:
        fail("add_anonymized_series_uid", str(e))

    # get_random
    try:
        r = await client.get_random_series()
        assert r.series_uid is not None
        ok("get_random_series")
    except Exception as e:
        fail("get_random_series", str(e))


async def test_record_types(client: ClarinetClient) -> None:
    """Test record type management."""
    print("\n--- 5. Record Types ---")

    # get_all (should have 3 from tasks/ loaded at startup)
    try:
        types = await client.get_record_types()
        type_names = [t.name for t in types]
        assert "doctor_review" in type_names
        assert "ai_analysis" in type_names
        assert "expert_check" in type_names
        ok(f"get_record_types ({len(types)} found: {', '.join(type_names)})")
    except Exception as e:
        fail("get_record_types", str(e))

    # find
    try:
        found = await client.find_record_types({"name": "doctor"})
        assert len(found) > 0
        ok(f"find_record_types ({len(found)} found)")
    except Exception as e:
        fail("find_record_types", str(e))

    # create a new one
    try:
        new_type = await client.create_record_type({
            "name": "test_record_type",
            "description": "Test record type for testing",
            "level": "SERIES",
            "data_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        })
        assert new_type.name == "test_record_type"
        ok("create_record_type")
    except ClarinetAPIError:
        ok("create_record_type (already exists)")
    except Exception as e:
        fail("create_record_type", str(e))


async def test_records(client: ClarinetClient) -> None:
    """Test record management."""
    print("\n--- 6. Records ---")

    # create
    try:
        rec = await client.create_record({
            "record_type_name": "doctor_review",
            "patient_id": "TEST_P001",
            "study_uid": "1.2.840.99999.1",
            "series_uid": "1.2.840.99999.1.1",
        })
        record_id = rec.id
        ok(f"create_record (id={record_id})")
    except Exception as e:
        fail("create_record", str(e))
        return  # Can't continue without a record

    # get_all
    try:
        records = await client.get_records()
        assert len(records) > 0
        ok(f"get_records ({len(records)} total)")
    except Exception as e:
        fail("get_records", str(e))

    # get_my_records
    try:
        my = await client.get_my_records()
        ok(f"get_my_records ({len(my)} found)")
    except Exception as e:
        fail("get_my_records", str(e))

    # get_my_pending
    try:
        pending = await client.get_my_pending_records()
        ok(f"get_my_pending_records ({len(pending)} found)")
    except Exception as e:
        fail("get_my_pending_records", str(e))

    # assign to user
    try:
        me = await client.get_me()
        assigned = await client.assign_record_to_user(record_id, me.id)
        assert assigned.user_id == me.id
        ok("assign_record_to_user")
    except Exception as e:
        fail("assign_record_to_user", str(e))

    # update status to inwork
    try:
        updated = await client.update_record_status(record_id, RecordStatus.inwork)
        assert updated.status == RecordStatus.inwork
        ok("update_record_status (inwork)")
    except Exception as e:
        fail("update_record_status", str(e))

    # submit data (this also sets status to finished)
    try:
        result = await client.submit_record_data(record_id, {
            "diagnosis": "Normal",
            "confidence": 85,
            "requires_expert": False,
            "notes": "No abnormalities detected",
        })
        assert result.data is not None
        assert result.data["diagnosis"] == "Normal"
        ok("submit_record_data")
    except Exception as e:
        fail("submit_record_data", str(e))


async def test_advanced_search(client: ClarinetClient) -> None:
    """Test advanced record search."""
    print("\n--- 7. Advanced Search ---")

    # by record_type_name
    try:
        found = await client.find_records_advanced(record_type_name="doctor_review")
        assert len(found) > 0
        ok(f"find_records_advanced by type ({len(found)} found)")
    except Exception as e:
        fail("find_records_advanced by type", str(e))

    # by study_uid
    try:
        found = await client.find_records_advanced(study_uid="1.2.840.99999.1")
        ok(f"find_records_advanced by study ({len(found)} found)")
    except Exception as e:
        fail("find_records_advanced by study", str(e))

    # by status
    try:
        found = await client.find_records_advanced(record_status=RecordStatus.pending)
        ok(f"find_records_advanced by status ({len(found)} pending)")
    except Exception as e:
        fail("find_records_advanced by status", str(e))

    # by patient_id
    try:
        found = await client.find_records_advanced(patient_id="TEST_P001")
        ok(f"find_records_advanced by patient ({len(found)} found)")
    except Exception as e:
        fail("find_records_advanced by patient", str(e))


async def test_study_hierarchy(client: ClarinetClient) -> None:
    """Test study hierarchy retrieval."""
    print("\n--- 8. Study Hierarchy ---")

    try:
        hierarchy = await client.get_study_hierarchy("1.2.840.99999.1")
        assert "study" in hierarchy
        assert "patient" in hierarchy
        assert "series" in hierarchy
        assert "records" in hierarchy
        ok(
            f"get_study_hierarchy "
            f"(series={len(hierarchy['series'])}, records={len(hierarchy['records'])})"
        )
    except Exception as e:
        fail("get_study_hierarchy", str(e))


async def test_batch_operations(client: ClarinetClient) -> None:
    """Test batch creation operations."""
    print("\n--- 9. Batch Operations ---")

    # create_studies_batch
    try:
        studies = await client.create_studies_batch([
            {"study_uid": "1.2.840.88888.1", "date": "2024-07-01", "patient_id": "TEST_P001"},
            {"study_uid": "1.2.840.88888.2", "date": "2024-07-02", "patient_id": "TEST_P001"},
        ])
        ok(f"create_studies_batch ({len(studies)} created)")
    except Exception as e:
        fail("create_studies_batch", str(e))

    # create_series_batch
    try:
        series = await client.create_series_batch([
            {"series_uid": "1.2.840.88888.1.1", "series_number": 1, "study_uid": "1.2.840.88888.1"},
            {"series_uid": "1.2.840.88888.1.2", "series_number": 2, "study_uid": "1.2.840.88888.1"},
        ])
        ok(f"create_series_batch ({len(series)} created)")
    except Exception as e:
        fail("create_series_batch", str(e))

    # create_patient_with_studies
    try:
        patient, studies = await client.create_patient_with_studies(
            patient_data={"patient_id": "BATCH_P001", "patient_name": "Batch Patient"},
            studies_data=[
                {"study_uid": "1.2.840.77777.1", "date": "2024-08-01"},
                {"study_uid": "1.2.840.77777.2", "date": "2024-08-02"},
            ],
        )
        ok(f"create_patient_with_studies (patient={patient.id}, studies={len(studies)})")
    except ClarinetAPIError:
        ok("create_patient_with_studies (already exists)")
    except Exception as e:
        fail("create_patient_with_studies", str(e))


async def test_recordflow(client: ClarinetClient) -> None:
    """Test RecordFlow workflow automation."""
    print("\n--- 10. RecordFlow ---")

    # Create a fresh series for RecordFlow testing
    try:
        await client.create_study({
            "study_uid": "1.2.840.55555.1",
            "date": "2024-09-01",
            "patient_id": "TEST_P001",
        })
    except ClarinetAPIError:
        pass  # already exists

    try:
        await client.create_series({
            "series_uid": "1.2.840.55555.1.1",
            "series_number": 1,
            "study_uid": "1.2.840.55555.1",
        })
    except ClarinetAPIError:
        pass  # already exists

    # Create a doctor_review record
    try:
        rec = await client.create_record({
            "record_type_name": "doctor_review",
            "patient_id": "TEST_P001",
            "study_uid": "1.2.840.55555.1",
            "series_uid": "1.2.840.55555.1.1",
        })
        flow_record_id = rec.id
        ok(f"create doctor_review for flow (id={flow_record_id})")
    except Exception as e:
        fail("create doctor_review for flow", str(e))
        return

    # Submit data with LOW confidence to trigger both flows
    try:
        result = await client.submit_record_data(flow_record_id, {
            "diagnosis": "Suspected lesion",
            "confidence": 50,
            "requires_expert": True,
            "notes": "Low confidence, need expert",
        })
        ok("submit_record_data (low confidence to trigger flows)")
    except Exception as e:
        fail("submit_record_data for flow", str(e))
        return

    # Wait for background tasks to complete
    print("  Waiting for RecordFlow background tasks...")
    await asyncio.sleep(3)

    # Check if ai_analysis was auto-created (Flow 1: unconditional on doctor_review finish)
    try:
        ai_records = await client.find_records_advanced(
            series_uid="1.2.840.55555.1.1",
            record_type_name="ai_analysis",
        )
        if len(ai_records) > 0:
            ok(f"RecordFlow: ai_analysis auto-created ({len(ai_records)} found)")
        else:
            fail("RecordFlow: ai_analysis NOT auto-created")
    except Exception as e:
        fail("RecordFlow: check ai_analysis", str(e))

    # Check if expert_check was auto-created (Flow 2: confidence < 70)
    try:
        expert_records = await client.find_records_advanced(
            series_uid="1.2.840.55555.1.1",
            record_type_name="expert_check",
        )
        if len(expert_records) > 0:
            ok(f"RecordFlow: expert_check auto-created ({len(expert_records)} found)")
        else:
            fail("RecordFlow: expert_check NOT auto-created (confidence < 70 should trigger)")
    except Exception as e:
        fail("RecordFlow: check expert_check", str(e))


async def test_record_data_submit(client: ClarinetClient) -> None:
    """Test structured data submission matching schemas."""
    print("\n--- 11. Record Data Submit ---")

    # Create a fresh record for data submit test
    try:
        await client.create_study({
            "study_uid": "1.2.840.44444.1",
            "date": "2024-10-01",
            "patient_id": "TEST_P001",
        })
    except ClarinetAPIError:
        pass

    try:
        await client.create_series({
            "series_uid": "1.2.840.44444.1.1",
            "series_number": 1,
            "study_uid": "1.2.840.44444.1",
        })
    except ClarinetAPIError:
        pass

    # doctor_review with full schema
    try:
        rec = await client.create_record({
            "record_type_name": "doctor_review",
            "patient_id": "TEST_P001",
            "study_uid": "1.2.840.44444.1",
            "series_uid": "1.2.840.44444.1.1",
        })
        result = await client.submit_record_data(rec.id, {
            "diagnosis": "Benign finding",
            "confidence": 92,
            "requires_expert": False,
            "notes": "Clear benign pattern observed",
        })
        assert result.data["confidence"] == 92
        ok("submit doctor_review data")
    except Exception as e:
        fail("submit doctor_review data", str(e))

    # Wait for flow to create ai_analysis
    await asyncio.sleep(2)

    # Find and submit ai_analysis data
    try:
        ai_records = await client.find_records_advanced(
            series_uid="1.2.840.44444.1.1",
            record_type_name="ai_analysis",
        )
        if ai_records:
            ai_rec = ai_records[0]
            result = await client.submit_record_data(ai_rec.id, {
                "ai_diagnosis": "Benign finding",
                "ai_confidence": 0.95,
                "findings": "No suspicious features detected by AI",
            })
            assert result.data["ai_confidence"] == 0.95
            ok("submit ai_analysis data")
        else:
            fail("submit ai_analysis data", "no ai_analysis record found")
    except Exception as e:
        fail("submit ai_analysis data", str(e))


async def main() -> None:
    print("=== Clarinet Demo: Functionality Tests ===")

    async with ClarinetClient(
        BASE_URL, username="admin", password="admin123"
    ) as client:
        await test_auth(client)
        await test_patients(client)
        await test_studies(client)
        await test_series(client)
        await test_record_types(client)
        await test_records(client)
        await test_advanced_search(client)
        await test_study_hierarchy(client)
        await test_batch_operations(client)
        await test_recordflow(client)
        await test_record_data_submit(client)

    # Summary
    total = passed + failed
    print(f"\n{'=' * 50}")
    print(f"Results: {passed}/{total} passed, {failed}/{total} failed")
    if errors:
        print("\nFailures:")
        for e in errors:
            print(f"  {e}")
    print(f"{'=' * 50}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
