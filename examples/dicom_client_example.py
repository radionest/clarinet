"""Example usage of DICOM client."""

import asyncio
from pathlib import Path

from src.services.dicom import (
    DicomClient,
    DicomNode,
    StudyQuery,
)
from src.settings import settings


async def example_find_and_retrieve() -> None:
    """Example: Find studies and retrieve to disk."""
    # Create DICOM client
    client = DicomClient(
        calling_aet=settings.dicom_aet,
        max_pdu=settings.dicom_max_pdu,
    )

    # Define PACS server
    pacs = DicomNode(
        aet="PACS_SERVER",
        host="192.168.1.100",
        port=11112,
    )

    # Find studies for patient
    studies = await client.find_studies(
        query=StudyQuery(patient_id="12345"),
        peer=pacs,
    )

    print(f"Found {len(studies)} studies:")
    for study in studies:
        print(f"  - {study.study_instance_uid}: {study.study_description}")

    # Retrieve first study to disk
    if studies:
        study = studies[0]
        output_dir = Path(settings.storage_path) / "dicom" / study.study_instance_uid

        result = await client.get_study(
            study_uid=study.study_instance_uid,
            peer=pacs,
            output_dir=output_dir,
            patient_id=study.patient_id,
        )

        print(
            f"\nRetrieved {result.num_completed} instances, "
            f"{result.num_failed} failed"
        )


async def example_find_and_move() -> None:
    """Example: Find studies and move to another server."""
    client = DicomClient(calling_aet=settings.dicom_aet)

    # Define source PACS
    source_pacs = DicomNode(
        aet="SOURCE_PACS",
        host="192.168.1.100",
        port=11112,
    )

    # Find studies
    studies = await client.find_studies(
        query=StudyQuery(
            patient_name="Doe^John",
            study_date="20240101-20240131",  # January 2024
        ),
        peer=source_pacs,
    )

    # Move to destination
    destination_aet = "DEST_PACS"

    for study in studies:
        result = await client.move_study(
            study_uid=study.study_instance_uid,
            peer=source_pacs,
            destination_aet=destination_aet,
            patient_id=study.patient_id,
        )

        print(
            f"Moved {study.study_instance_uid}: "
            f"{result.num_completed} completed, {result.num_failed} failed"
        )


async def example_retrieve_to_memory() -> None:
    """Example: Retrieve study to memory for processing."""
    client = DicomClient(calling_aet=settings.dicom_aet)

    pacs = DicomNode(
        aet="PACS_SERVER",
        host="192.168.1.100",
        port=11112,
    )

    # Retrieve to memory
    result = await client.get_study_to_memory(
        study_uid="1.2.840.113619.2.1.1.1",
        peer=pacs,
    )

    print(f"Retrieved {len(result.instances)} instances to memory")

    # Process instances
    for ds in result.instances:
        print(f"  - {ds.SOPInstanceUID}: {ds.Modality}")


async def example_find_series() -> None:
    """Example: Find series in a study."""
    client = DicomClient(calling_aet=settings.dicom_aet)

    pacs = DicomNode(
        aet="PACS_SERVER",
        host="192.168.1.100",
        port=11112,
    )

    from src.services.dicom import SeriesQuery

    # Find all CT series in study
    series = await client.find_series(
        query=SeriesQuery(
            study_instance_uid="1.2.840.113619.2.1.1.1",
            modality="CT",
        ),
        peer=pacs,
    )

    print(f"Found {len(series)} CT series:")
    for s in series:
        print(
            f"  - Series {s.series_number}: {s.series_description} "
            f"({s.number_of_series_related_instances} instances)"
        )


if __name__ == "__main__":
    # Run example
    asyncio.run(example_find_and_retrieve())
