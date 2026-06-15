"""Tests for the DB scrubber (``clarinet anon scrub-db``).

Pure-function coverage for the schema-aware JSON scrub and the PHI audit, plus
end-to-end :class:`DbScrubber` runs on the SQLite test engine (FK enforcement
on) asserting the fixture deliverable: MRN gone everywhere, structural data and
anon identifiers preserved, the ``FileRepository`` path still resolves, and the
audit stays green.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from clarinet.cli.anon_scrub import _parse_keep
from clarinet.models.auth import AccessToken
from clarinet.models.base import DicomQueryLevel
from clarinet.models.counter import AutoIdCounter
from clarinet.models.patient import Patient
from clarinet.models.pipeline_task_run import PipelineTaskRun
from clarinet.models.record import Record
from clarinet.models.record_event import RecordEvent
from clarinet.models.study import Series, Study
from clarinet.models.user import User
from clarinet.services.common.storage_paths import build_context, render_working_folder
from clarinet.services.db_scrub import (
    DbScrubber,
    PhiLeakError,
    collect_phi_terms,
    scan_json,
    scan_text,
    scrub_record_data,
)
from clarinet.settings import settings
from tests.utils.factories import make_patient, make_record_type, make_study, make_user

_DEFAULT_TEMPLATE = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_good": {"type": "boolean"},
        "comment": {"type": "string"},  # free text — scrub
        "best_series": {"type": "string", "x-options": {"source": "study_series"}},  # keep
        "lesion_count": {"type": "integer"},  # keep
        "study_type": {"type": "string", "enum": ["CT", "MRI"]},  # keep
    },
}


# ── json_scrub (pure) ────────────────────────────────────────────────


def test_scrub_preserves_structural_redacts_free_text() -> None:
    data = {
        "is_good": True,
        "comment": "note by Ivanov",
        "best_series": "1.2.840.5",
        "lesion_count": 3,
        "study_type": "CT",
    }
    out = scrub_record_data(data, _SCHEMA)
    assert out == {
        "is_good": True,
        "comment": "",
        "best_series": "1.2.840.5",
        "lesion_count": 3,
        "study_type": "CT",
    }
    assert data["comment"] == "note by Ivanov"  # input not mutated


def test_scrub_none_passthrough() -> None:
    assert scrub_record_data(None, _SCHEMA) is None


def test_scrub_unknown_key_is_treated_as_free_text() -> None:
    out = scrub_record_data({"surprise": "Ivanov Ivan"}, _SCHEMA)
    assert out == {"surprise": ""}


def test_scrub_without_schema_redacts_strings_keeps_numbers() -> None:
    out = scrub_record_data({"note": "secret", "count": 7, "ok": False}, None)
    assert out == {"note": "", "count": 7, "ok": False}


def test_scrub_recurses_arrays_of_objects() -> None:
    schema = {
        "type": "object",
        "properties": {
            "lesions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lesion_num": {"type": "integer"},
                        "description": {"type": "string"},
                    },
                },
            }
        },
    }
    data = {"lesions": [{"lesion_num": 1, "description": "left lobe Ivanov"}]}
    out = scrub_record_data(data, schema)
    assert out == {"lesions": [{"lesion_num": 1, "description": ""}]}


def test_scrub_honours_if_then_branch_enum() -> None:
    schema = {
        "type": "object",
        "properties": {"is_good": {"type": "boolean"}},
        "then": {"properties": {"study_type": {"type": "string", "enum": ["CT"]}}},
    }
    out = scrub_record_data({"is_good": True, "study_type": "CT"}, schema)
    assert out == {"is_good": True, "study_type": "CT"}


# ── audit (pure) ─────────────────────────────────────────────────────


def test_collect_phi_terms_splits_names_and_keeps_ids_verbatim() -> None:
    terms = collect_phi_terms(["Ivanov Ivan", "Al", None, ""], ["MRN12345", "7"])
    # Name "Al" dropped (too short); ids kept verbatim incl. the 1-char MRN.
    assert terms == {"ivanov", "ivan", "mrn12345", "7"}


def test_scan_text_whole_word_only() -> None:
    terms = {"ivanov", "mrn12345"}
    assert scan_text("Report by Ivanov", terms) == {"ivanov"}
    assert scan_text("anon CLARINET_12345 ok", {"12345"}) == set()  # no boundary at '_'
    assert scan_text("nothing here", terms) == set()


def test_scan_json_only_string_leaves() -> None:
    # A numeric MRN must not match a preserved integer of the same digits.
    assert scan_json({"count": 12345, "note": "ok"}, {"12345"}) == set()
    assert scan_json({"note": "case 12345 detail"}, {"12345"}) == {"12345"}


def test_parse_keep_all_and_csv() -> None:
    assert _parse_keep("all") is None
    assert _parse_keep("A, B ,C") == {"A", "B", "C"}
    with pytest.raises(ValueError):
        _parse_keep(" , ")


# ── DbScrubber (integration, SQLite) ─────────────────────────────────


async def _seed_graph(session: AsyncSession) -> dict[str, str]:
    """Seed a kept patient + an out-of-scope patient with full relations."""
    keep = make_patient("MRN12345", "Ivanov Petrov", auto_id=42)
    drop = make_patient("OTHER99", "Sidorov", auto_id=43)
    session.add(keep)
    session.add(drop)
    await session.commit()

    keep_study = Study(
        patient_id="MRN12345",
        study_uid="1.2.3.100",
        date=datetime.now(UTC).date(),
        modalities_in_study="CT",
        study_description="CT abdomen Ivanov",
        anon_uid="2.25.900",
    )
    drop_study = make_study("OTHER99", uid="1.2.3.200")
    session.add(keep_study)
    session.add(drop_study)
    await session.commit()

    series = Series(
        study_uid="1.2.3.100",
        series_uid="1.2.3.100.1",
        series_number=1,
        modality="CT",
        series_description="arterial phase",
        anon_uid="2.25.900.1",
    )
    session.add(series)

    rt = make_record_type(name="first-check", data_schema=_SCHEMA)
    session.add(rt)
    user = make_user(email="doctor@hospital.com", is_superuser=False)
    admin = make_user(email="admin@clarinet.ru", is_superuser=True)
    session.add(user)
    session.add(admin)
    await session.commit()

    keep_rec = Record(
        patient_id="MRN12345",
        study_uid="1.2.3.100",
        series_uid="1.2.3.100.1",
        record_type_name="first-check",
        user_id=user.id,
        data={"is_good": True, "comment": "note by Ivanov", "best_series": "1.2.840.5"},
        context_info="Patient Ivanov Petrov MRN12345",
        clarinet_storage_path="/data/MRN12345/2.25.900",
    )
    drop_rec = Record(
        patient_id="OTHER99",
        study_uid="1.2.3.200",
        series_uid=None,
        record_type_name="first-check",
        data={"comment": "Sidorov note"},
    )
    session.add(keep_rec)
    session.add(drop_rec)
    await session.commit()

    session.add(
        RecordEvent(
            record_id=keep_rec.id,
            record_key=keep_rec.id,
            kind="deleted",
            old_value={
                "patient_id": "MRN12345",
                "record_type_name": "first-check",
                "context_info": "Ivanov Petrov",
                "data": {"is_good": True, "comment": "Ivanov secret"},
                "clarinet_storage_path": "/data/MRN12345/x",
            },
            reason="removed by Ivanov",
        )
    )
    session.add(
        PipelineTaskRun(
            id=str(uuid4()),
            task_name="convert_series_to_nifti",
            queue="default",
            patient_id="MRN12345",
            started_at=datetime.now(UTC),
            error_message="failed for MRN12345 (Ivanov)",
        )
    )
    session.add(
        PipelineTaskRun(
            id=str(uuid4()),
            task_name="convert_series_to_nifti",
            queue="default",
            patient_id="OTHER99",
            started_at=datetime.now(UTC),
        )
    )
    session.add(
        AccessToken(
            token="tok-123",
            user_id=admin.id,
            expires_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return {
        "new_id": f"{settings.anon_id_prefix}_42",
        "admin_email": "admin@clarinet.ru",
        "user_id": str(user.id),
    }


@pytest.mark.asyncio
async def test_scrub_db_end_to_end(test_session: AsyncSession) -> None:
    meta = await _seed_graph(test_session)
    new_id = meta["new_id"]

    report = await DbScrubber(test_session, keep_patient_ids={"MRN12345"}).run()
    test_session.expire_all()

    assert report.committed is True
    assert report.phi_hits == set()
    assert report.patients_kept == 1
    assert report.patients_deleted == 1

    # Out-of-scope patient and its graph are gone.
    assert (await test_session.execute(select(Patient.id))).scalars().all() == [new_id]
    assert (await test_session.execute(select(func.count()).select_from(Study))).scalar() == 1
    assert (await test_session.execute(select(func.count()).select_from(AccessToken))).scalar() == 0

    patient = (
        await test_session.execute(select(Patient).where(col(Patient.id) == new_id))
    ).scalar_one()
    assert patient.name == "Patient_42"
    assert patient.auto_id == 42  # preserved → anon_id stays CLARINET_42
    assert patient.anon_name == new_id  # regenerated deterministically, not preserved

    study = (
        await test_session.execute(select(Study).where(col(Study.study_uid) == "1.2.3.100"))
    ).scalar_one()
    assert study.patient_id == new_id  # FK repointed
    assert study.anon_uid == "2.25.900"  # preserved
    assert study.study_description is None

    series = (
        await test_session.execute(select(Series).where(col(Series.series_uid) == "1.2.3.100.1"))
    ).scalar_one()
    assert series.anon_uid == "2.25.900.1"  # preserved
    assert series.series_description is None

    record = (
        await test_session.execute(select(Record).where(col(Record.patient_id) == new_id))
    ).scalar_one()
    assert record.data == {"is_good": True, "comment": "", "best_series": "1.2.840.5"}
    assert record.context_info is None
    assert record.clarinet_storage_path is None

    event = (await test_session.execute(select(RecordEvent))).scalar_one()
    assert event.old_value["patient_id"] == new_id  # remapped MRN
    assert event.old_value["context_info"] is None
    assert event.old_value["data"] == {"is_good": True, "comment": ""}
    assert event.reason is None

    runs = (await test_session.execute(select(PipelineTaskRun))).scalars().all()
    assert len(runs) == 1  # out-of-scope run deleted
    assert runs[0].patient_id == new_id
    assert runs[0].error_message is None

    user_emails = (await test_session.execute(select(User.email))).scalars().all()
    assert meta["admin_email"] in user_emails  # superuser kept
    assert any(e.endswith("@example.invalid") for e in user_emails)  # doctor scrubbed
    hashes = (await test_session.execute(select(User.hashed_password))).scalars().all()
    assert all(h == "!scrubbed-no-login!" for h in hashes)  # credentials blanked (all roles)

    # Sequence/counter pinned to MAX(auto_id) so a new stand patient won't collide.
    counter = (
        await test_session.execute(
            select(AutoIdCounter).where(col(AutoIdCounter.name) == "patient_auto_id")
        )
    ).scalar_one()
    assert counter.last_value == 42

    # FileRepository path engine still resolves to the anonymized layout.
    ctx = build_context(patient=patient, study=study, series=series, template=_DEFAULT_TEMPLATE)
    series_dir = render_working_folder(
        _DEFAULT_TEMPLATE, DicomQueryLevel.SERIES, ctx, Path("/storage")
    )
    assert series_dir == Path("/storage") / new_id / "2.25.900" / "2.25.900.1"


@pytest.mark.asyncio
async def test_scrub_db_dry_run_rolls_back(test_session: AsyncSession) -> None:
    await _seed_graph(test_session)
    report = await DbScrubber(test_session, keep_patient_ids={"MRN12345"}, dry_run=True).run()
    test_session.expire_all()

    assert report.committed is False
    # Both patients still present, MRN intact — nothing persisted.
    ids = set((await test_session.execute(select(Patient.id))).scalars())
    assert ids == {"MRN12345", "OTHER99"}


@pytest.mark.asyncio
async def test_scrub_db_phi_leak_fails_and_rolls_back(test_session: AsyncSession) -> None:
    # best_series is x-options (preserved) — stuffing PHI there leaks past the
    # schema pass and must be caught by the audit.
    patient = make_patient("MRN777", "Volkov", auto_id=77)
    test_session.add(patient)
    await test_session.commit()
    study = make_study("MRN777", uid="1.2.7")
    study.anon_uid = "2.25.7"
    test_session.add(study)
    await test_session.commit()
    rt = make_record_type(name="first-check", data_schema=_SCHEMA)
    test_session.add(rt)
    await test_session.commit()
    rec = Record(
        patient_id="MRN777",
        study_uid="1.2.7",
        series_uid=None,
        record_type_name="first-check",
        data={"best_series": "Volkov"},  # PHI in a preserved field
    )
    test_session.add(rec)
    await test_session.commit()

    with pytest.raises(PhiLeakError, match="volkov"):
        await DbScrubber(test_session, keep_patient_ids={"MRN777"}).run()
    test_session.expire_all()

    # Rolled back → original MRN still present.
    patient_after = (
        await test_session.execute(select(Patient).where(col(Patient.id) == "MRN777"))
    ).scalar_one()
    assert patient_after.name == "Volkov"
