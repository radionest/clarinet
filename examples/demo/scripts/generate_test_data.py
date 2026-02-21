"""
Generate test data for the Clarinet demo project.

Creates patients, studies, series, and doctor_review records
using the ClarinetClient async API.

Usage:
    cd examples/demo
    python scripts/generate_test_data.py
"""

import asyncio
import random
import sys
from pathlib import Path

# Add project root to path so we can import src
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.client import ClarinetAPIError, ClarinetClient

BASE_URL = "http://localhost:8000/api"

PATIENTS = [
    {"patient_id": "PAT001", "patient_name": "Иванов Иван Иванович"},
    {"patient_id": "PAT002", "patient_name": "Петрова Мария Сергеевна"},
    {"patient_id": "PAT003", "patient_name": "Сидоров Алексей Николаевич"},
    {"patient_id": "PAT004", "patient_name": "Козлова Елена Дмитриевна"},
    {"patient_id": "PAT005", "patient_name": "Морозов Дмитрий Александрович"},
]


def generate_uid(base: str, suffix: int) -> str:
    """Generate a DICOM-style UID."""
    return f"1.2.840.{base}.{suffix}"


async def main() -> None:
    print("=== Clarinet Demo: Test Data Generator ===\n")

    async with ClarinetClient(
        BASE_URL, username="admin@clarinet.ru", password="admin123"
    ) as client:
        me = await client.get_me()
        print(f"Logged in as: {me.email}\n")

        # Create patients
        print("--- Creating patients ---")
        created_patients = []
        for p in PATIENTS:
            try:
                patient = await client.create_patient(p)
                print(f"  Created patient: {patient.id} ({patient.name})")
                created_patients.append(patient)
            except ClarinetAPIError as e:
                print(f"  Patient {p['patient_id']} already exists or error: {e.message}")

        # Create studies and series
        print("\n--- Creating studies and series ---")
        all_series_uids: list[tuple[str, str, str]] = []  # (series_uid, study_uid, patient_id)

        for i, patient in enumerate(created_patients):
            num_studies = random.randint(1, 2)
            for s in range(num_studies):
                study_uid = generate_uid(f"11111.{i + 1}", s + 1)
                study_date = f"2024-{(i + 1):02d}-{(s + 1) * 10:02d}"

                try:
                    study = await client.create_study(
                        {
                            "study_uid": study_uid,
                            "date": study_date,
                            "patient_id": patient.id,
                        }
                    )
                    print(f"  Study: {study.study_uid} for {patient.id}")
                except ClarinetAPIError as e:
                    print(f"  Study {study_uid} error: {e.message}")
                    continue

                num_series = random.randint(2, 3)
                for sr in range(num_series):
                    series_uid = generate_uid(f"11111.{i + 1}.{s + 1}", sr + 1)
                    descriptions = ["T1 axial", "T2 coronal", "DWI", "FLAIR", "T1 post-contrast"]

                    try:
                        series = await client.create_series(
                            {
                                "series_uid": series_uid,
                                "series_number": sr + 1,
                                "study_uid": study_uid,
                                "series_description": random.choice(descriptions),
                            }
                        )
                        print(f"    Series: {series.series_uid} (#{sr + 1})")
                        all_series_uids.append((series_uid, study_uid, patient.id))
                    except ClarinetAPIError as e:
                        print(f"    Series {series_uid} error: {e.message}")

        # Create doctor_review records for each series
        print("\n--- Creating doctor_review records ---")
        for series_uid, study_uid, patient_id in all_series_uids:
            try:
                record = await client.create_record(
                    {
                        "record_type_name": "doctor_review",
                        "patient_id": patient_id,
                        "study_uid": study_uid,
                        "series_uid": series_uid,
                    }
                )
                print(f"  Record #{record.id}: doctor_review for series {series_uid}")
            except ClarinetAPIError as e:
                print(f"  Record error for {series_uid}: {e.message}")

        # Summary
        patients = await client.get_patients()
        studies = await client.get_studies()
        records = await client.get_records()

        print("\n=== Summary ===")
        print(f"  Patients: {len(patients)}")
        print(f"  Studies:  {len(studies)}")
        print(f"  Series:   {len(all_series_uids)}")
        print(f"  Records:  {len(records)}")
        print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
