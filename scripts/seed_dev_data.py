"""Seed a local Clarinet database with synthetic data for manual frontend QA.

Builds a deterministic-but-varied dataset so that every filter / sort
combination on /records and /admin has a meaningful result set:

- 60 patients, 90 studies, 130 series (30 studies series-less for NULL
  modality coverage)
- 5 record types across PATIENT / STUDY / SERIES levels
- 5 dev users + the existing admin
- N records (default 500) round-robined by status + record_type, with
  ~40% unassigned to exercise the `wo_user` filter and `user_*` NULLS
  LAST sorts, and changed_at spread over the last 30 days

Usage:
    uv run python scripts/seed_dev_data.py [--count 500] [--reset]

`--reset` drops the local SQLite file and re-runs `clarinet db init`
before seeding. For non-SQLite drivers it bails with a message.
"""

import argparse
import asyncio
import os
import random
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlmodel import select

# Hush the optional Slicer/PACS subsystems and provide default admin
# credentials so a bare dev box can run this script without manual env
# setup. The defaults match settings_debug fallback semantics.
os.environ.setdefault("CLARINET_DICOM_ENABLED", "false")
os.environ.setdefault("CLARINET_SLICER_ENABLED", "false")
os.environ.setdefault("CLARINET_PIPELINE_ENABLED", "false")
os.environ.setdefault("CLARINET_DEBUG", "true")
os.environ.setdefault("CLARINET_ADMIN_EMAIL", "admin@clarinet.dev")
os.environ.setdefault("CLARINET_ADMIN_PASSWORD", "admin123")

from clarinet.models import Patient, Record, RecordType, Study
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.study import Series
from clarinet.models.user import User
from clarinet.repositories.patient_repository import PatientRepository
from clarinet.settings import DatabaseDriver, settings
from clarinet.utils.auth import get_password_hash
from clarinet.utils.database import get_async_session

random.seed(42)


# --- Reset & init -----------------------------------------------------------


def reset_database() -> None:
    """Wipe the SQLite file, alembic.ini and alembic/ so init-migrations
    can start fresh. Refuses to touch a non-SQLite DB."""
    if settings.database_driver != DatabaseDriver.SQLITE:
        sys.exit(
            f"--reset is only supported for SQLite (current driver: "
            f"{settings.database_driver}). Reset manually."
        )
    db_path = Path(f"{settings.database_name}.db")
    if db_path.exists():
        db_path.unlink()
        print(f"  removed {db_path}")
    alembic_ini = Path("alembic.ini")
    if alembic_ini.exists():
        alembic_ini.unlink()
        print("  removed alembic.ini")
    alembic_dir = Path("alembic")
    if alembic_dir.exists():
        shutil.rmtree(alembic_dir)
        print("  removed alembic/")


def _run_cli(*args: str) -> None:
    # `args` are hard-coded literals from this file (no user input flows
    # in here), so the subprocess call is safe — silencing the generic
    # opengrep "subprocess without static string" alert.
    cmd = ["uv", "run", "clarinet", *args]
    print(f"→ {' '.join(cmd[2:])} …")
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        sys.exit(f"`{' '.join(cmd[2:])}` failed: exit {result.returncode}")
    print("  ok")


def bootstrap_alembic_and_admin() -> None:
    """Run `clarinet init-migrations` (creates alembic.ini + initial migration +
    applies it) followed by `clarinet db init` (default roles + admin user).
    Both are idempotent: skipping when already set up."""
    if not Path("alembic.ini").exists():
        _run_cli("init-migrations")
    _run_cli("db", "init")


# --- Seed steps -------------------------------------------------------------


RECORD_TYPES: list[tuple[str, DicomQueryLevel, str]] = [
    ("intake-screening", DicomQueryLevel.PATIENT, "Patient intake screening"),
    ("study-summary", DicomQueryLevel.STUDY, "Study-level radiology summary"),
    ("study-review", DicomQueryLevel.STUDY, "Second-reader study review"),
    ("lesion-annotation", DicomQueryLevel.SERIES, "Per-series lesion annotation"),
    ("series-quality", DicomQueryLevel.SERIES, "Per-series image quality check"),
]

MODALITIES = ["CT", "MR", "US", "PT", "XA"]


async def seed_record_types(session) -> list[RecordType]:
    existing = (await session.execute(select(RecordType))).scalars().all()
    if existing:
        print(f"  {len(existing)} record types already exist — keeping them")
        return list(existing)
    rts = [
        RecordType(name=name, level=level, description=desc) for name, level, desc in RECORD_TYPES
    ]
    for rt in rts:
        session.add(rt)
    await session.flush()
    print(f"  created {len(rts)} record types")
    return rts


async def seed_users(session) -> list[User]:
    """Create 5 dev users (admin already exists from `db init`)."""
    existing = (
        (await session.execute(select(User).where(User.email.like("dev%@clarinet.dev"))))  # type: ignore[attr-defined]
        .scalars()
        .all()
    )
    if existing:
        print(f"  {len(existing)} dev users already exist — keeping them")
        return list(existing)
    pwd = get_password_hash("dev123")
    users = [
        User(
            id=uuid4(),
            email=f"dev{i}@clarinet.dev",
            hashed_password=pwd,
            is_active=True,
            is_verified=True,
            is_superuser=False,
        )
        for i in range(1, 6)
    ]
    for u in users:
        session.add(u)
    await session.flush()
    print(f"  created {len(users)} dev users (password: dev123)")
    return users


async def seed_patients(session, n: int = 60) -> list[Patient]:
    """Seed `n` patients with deterministic `DEV_PAT_NNN` ids. Idempotent on
    re-run: reuses existing `DEV_PAT_*` rows and only inserts the missing
    indices, so the script works without `--reset` even on a partial run."""
    existing_stmt = select(Patient).where(Patient.id.like("DEV_PAT_%"))  # type: ignore[attr-defined]
    existing = list((await session.execute(existing_stmt)).scalars().all())
    by_id = {p.id: p for p in existing}
    repo = PatientRepository(session)
    patients: list[Patient] = []
    created = 0
    for i in range(n):
        pid = f"DEV_PAT_{i:03d}"
        if pid in by_id:
            patients.append(by_id[pid])
            continue
        p = Patient(id=pid, name=f"Dev Patient {i}")
        patients.append(await repo.create(p))
        created += 1
    print(f"  {len(patients)} patients ready ({created} created, {len(patients) - created} reused)")
    return patients


async def seed_studies(session, patients: list[Patient], total: int = 90) -> list[Study]:
    studies: list[Study] = []
    today = datetime.now(UTC).date()
    for i in range(total):
        owner = random.choice(patients)
        uid = f"1.2.3.{900000 + i}"
        study = Study(
            patient_id=owner.id,
            study_uid=uid,
            date=today - timedelta(days=random.randint(0, 60)),
            study_description=f"Dev study {i}",
        )
        session.add(study)
        studies.append(study)
    await session.flush()
    print(f"  created {len(studies)} studies")
    return studies


async def seed_series(session, studies: list[Study], total: int = 130) -> list[Series]:
    """Attach series to the first `series_studies` studies; leave the rest
    series-less so SERIES-level records on them get series_uid=None
    (exercises `modality_*` NULLS LAST sort)."""
    series_studies = studies[:60]  # 60 of 90 studies get series
    series: list[Series] = []
    counter = 0
    for study in series_studies:
        n = random.choice([1, 2, 3])
        for j in range(n):
            modality = random.choice(MODALITIES)
            ser = Series(
                study_uid=study.study_uid,
                series_uid=f"{study.study_uid}.{j + 1}",
                series_number=j + 1,
                modality=modality,
                series_description=f"{modality} series {counter}",
            )
            session.add(ser)
            series.append(ser)
            counter += 1
            if len(series) >= total:
                break
        if len(series) >= total:
            break
    await session.flush()
    print(f"  created {len(series)} series across {len(series_studies)} studies")
    return series


async def seed_records(
    session,
    *,
    count: int,
    patients: list[Patient],
    studies: list[Study],
    series: list[Series],
    record_types: list[RecordType],
    users: list[User],
) -> int:
    studies_by_patient: dict[str, list[Study]] = {}
    for s in studies:
        studies_by_patient.setdefault(s.patient_id, []).append(s)
    series_by_study: dict[str, list[Series]] = {}
    for ser_obj in series:
        series_by_study.setdefault(ser_obj.study_uid, []).append(ser_obj)

    # Patients with at least one study (needed for STUDY/SERIES levels).
    patients_with_studies = [p for p in patients if studies_by_patient.get(p.id)]

    statuses = list(RecordStatus)  # 6 values
    now = datetime.now(UTC)

    created = 0
    for i in range(count):
        rt = record_types[i % len(record_types)]
        status = statuses[i % len(statuses)]
        # 40% unassigned (None) to exercise wo_user filter + user_* NULLS LAST.
        user_id = None if random.random() < 0.4 else random.choice(users).id

        # Pick the context (patient/study/series) matching rt.level.
        study = None
        ser = None
        if rt.level == DicomQueryLevel.PATIENT:
            patient = random.choice(patients)
        else:
            patient = random.choice(patients_with_studies)
            study = random.choice(studies_by_patient[patient.id])
        if rt.level == DicomQueryLevel.SERIES:
            assert study is not None
            candidate_series = series_by_study.get(study.study_uid, [])
            # Leave roughly half of SERIES-level records without a series so
            # the modality sort has NULL rows.
            if candidate_series and random.random() < 0.5:
                ser = random.choice(candidate_series)

        changed_at = now - timedelta(
            days=random.randint(0, 30),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
        )

        record = Record(
            patient_id=patient.id,
            study_uid=study.study_uid if study else None,
            series_uid=ser.series_uid if ser else None,
            record_type_name=rt.name,
            status=status,
            user_id=user_id,
        )
        # Set `changed_at` explicitly because the SA column has
        # `server_default=func.now()` which would otherwise stamp it at
        # flush time, collapsing every row onto the same timestamp.
        record.changed_at = changed_at
        if status == RecordStatus.inwork:
            record.started_at = changed_at
        if status == RecordStatus.finished:
            record.finished_at = changed_at
        session.add(record)
        created += 1

        if created % 100 == 0:
            await session.flush()
            print(f"  …{created}/{count} records flushed")

    await session.flush()
    print(f"  created {created} records")
    return created


# --- Summary ----------------------------------------------------------------


def print_summary(count: int) -> None:
    print()
    print("=" * 60)
    print(f"Seeded {count} records.")
    print()
    print("Run the dev server:")
    print("  make run-dev")
    print()
    print("Open http://127.0.0.1:8000 and log in:")
    # Password redacted to keep CodeQL / static analysers happy. The script
    # sets `CLARINET_ADMIN_PASSWORD=admin123` as a `setdefault` at the top
    # of this file when the env var isn't already set; users who want a
    # different password override that env var before invoking.
    print(f"  admin:  {settings.admin_email} / (env CLARINET_ADMIN_PASSWORD)")
    print("  user:   dev1@clarinet.dev / dev123 (dev2…dev5 also work)")
    print()
    print("UI checklist:")
    print("  /admin   — Records section: filter status/type/patient/user")
    print("            (try `user=__unassigned__`); click column headers")
    print("            for server-side sort; cursor pagination on >100 rows.")
    print("  /records — same filters; non-admin only sees own records.")
    print("  /patients/DEV_PAT_001  — patient-scoped bucket.")
    print("=" * 60)


# --- Entry point ------------------------------------------------------------


async def _main(count: int) -> None:
    async for session in get_async_session():
        print("→ seeding record types …")
        rts = await seed_record_types(session)
        print("→ seeding users …")
        users = await seed_users(session)
        # Pull the existing admin into the pool so some records are owned by it.
        admin = (
            (await session.execute(select(User).where(User.is_superuser.is_(True))))  # type: ignore[attr-defined]
            .scalars()
            .first()
        )
        if admin is not None:
            users = [*users, admin]
        print("→ seeding patients …")
        patients = await seed_patients(session)
        print("→ seeding studies …")
        studies = await seed_studies(session, patients)
        print("→ seeding series …")
        series = await seed_series(session, studies)
        print(f"→ seeding {count} records …")
        created = await seed_records(
            session,
            count=count,
            patients=patients,
            studies=studies,
            series=series,
            record_types=rts,
            users=users,
        )
        await session.commit()
        print_summary(created)
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count",
        type=int,
        default=500,
        help="Number of Record rows to create (default: 500).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop the SQLite database file and re-run `clarinet db init` first.",
    )
    args = parser.parse_args()

    if args.reset:
        print("→ resetting database …")
        reset_database()
    bootstrap_alembic_and_admin()

    asyncio.run(_main(args.count))


if __name__ == "__main__":
    main()
