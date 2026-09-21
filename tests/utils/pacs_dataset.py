"""Synthetic DICOM dataset that the DICOM test suite seeds into its Orthanc.

The DICOM tests query a ``SHIPILOV*`` patient. That data used to exist only on
one developer's PACS, so everywhere else the fixtures failed with "No SHIPILOV
studies found on test PACS". The suite now brings its own: two tiny studies
built here and uploaded over Orthanc's REST API when the PACS has no
``SHIPILOV*`` study yet. A PACS that already holds one is never written to.

What the tests need from the data (pinned by ``tests/test_pacs_dataset.py``):
the SHIPILOV patient has exactly one study and it is MR, a CT study exists
under another patient, every series is a geometrically valid volume with real
pixel data, and UIDs are deterministic so seeding twice adds nothing (Orthanc
answers the duplicate upload ``AlreadyStored``).
"""

from dataclasses import dataclass
from io import BytesIO
from typing import Literal

import numpy as np
import numpy.typing as npt
import pydicom
import pytest
import requests
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import (
    UID,
    CTImageStorage,
    ExplicitVRLittleEndian,
    MRImageStorage,
    generate_uid,
)

from tests.config import PACS_HOST, PACS_REST_URL

PATIENT_NAME_PREFIX = "SHIPILOV"

_SLICES = 4
_SIZE = 64
_SLICE_SPACING_MM = 2.0
_UID_NAMESPACE = "clarinet-test-pacs-dataset"

_seeded = False


@dataclass(frozen=True, slots=True)
class _Study:
    patient_id: str
    patient_name: str
    modality: Literal["MR", "CT"]
    description: str
    accession: str
    series_descriptions: tuple[str, ...]


# Seeded in this order: the SHIPILOV study doubles as the "dataset is present"
# marker, so it goes last — a concurrent seeder never sees the marker without
# the CT study. The marker does flip on the first of the MR instances, so for
# the rest of the MR upload (well under a second) a second seeder — the slicer
# xdist group runs beside the dicom one — may skip seeding and read a partial
# study. Deterministic UIDs keep a double upload harmless.
_STUDIES = (
    _Study(
        patient_id="PHANTOM-CT-001",
        patient_name="PHANTOM^CT",
        modality="CT",
        description="CLARINET TEST CT",
        accession="CLTESTCT001",
        series_descriptions=("AXIAL CT",),
    ),
    _Study(
        patient_id="SHIPILOV-TEST-001",
        patient_name=f"{PATIENT_NAME_PREFIX}^TEST",
        modality="MR",
        description="CLARINET TEST MR",
        accession="CLTESTMR001",
        series_descriptions=("T1 AXIAL", "T2 AXIAL"),
    ),
)


def _uid(*parts: str) -> UID:
    return generate_uid(entropy_srcs=[_UID_NAMESPACE, *parts])


def _volume(modality: Literal["MR", "CT"]) -> npt.NDArray[np.int16] | npt.NDArray[np.uint16]:
    """A cube on a gradient — enough structure to see the volume loaded the right way up."""
    z, y, x = np.indices((_SLICES, _SIZE, _SIZE))
    volume = (x + y + 10 * z).astype(np.int32)
    quarter = _SIZE // 4
    volume[:, quarter:-quarter, quarter:-quarter] += 500
    if modality == "CT":
        return (volume - 1000).astype(np.int16)
    return volume.astype(np.uint16)


def _instance(
    study: _Study,
    series_number: int,
    series_description: str,
    index: int,
    pixels: npt.NDArray[np.int16] | npt.NDArray[np.uint16],
) -> FileDataset:
    sop_class = MRImageStorage if study.modality == "MR" else CTImageStorage
    sop_uid = _uid(study.patient_id, str(series_number), str(index))

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset("", {}, file_meta=meta, preamble=b"\x00" * 128)
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = sop_uid

    ds.PatientID = study.patient_id
    ds.PatientName = study.patient_name
    ds.PatientBirthDate = "19700101"
    ds.PatientSex = "O"

    ds.StudyInstanceUID = _uid(study.patient_id, "study")
    ds.StudyDate = "20260101"
    ds.StudyTime = "120000"
    ds.StudyID = "1"
    ds.StudyDescription = study.description
    ds.AccessionNumber = study.accession
    ds.ReferringPhysicianName = ""

    ds.SeriesInstanceUID = _uid(study.patient_id, "series", str(series_number))
    ds.SeriesNumber = series_number
    ds.SeriesDescription = series_description
    ds.SeriesDate = ds.StudyDate
    ds.SeriesTime = ds.StudyTime
    ds.Modality = study.modality
    ds.Manufacturer = "Clarinet tests"
    ds.PatientPosition = "HFS"
    ds.FrameOfReferenceUID = _uid(study.patient_id, "frame-of-reference")

    ds.InstanceNumber = index + 1
    ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
    ds.ImagePositionPatient = [0.0, 0.0, index * _SLICE_SPACING_MM]
    ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    ds.SliceLocation = index * _SLICE_SPACING_MM
    ds.SliceThickness = _SLICE_SPACING_MM
    ds.SpacingBetweenSlices = _SLICE_SPACING_MM
    ds.PixelSpacing = [1.0, 1.0]

    ds.Rows = _SIZE
    ds.Columns = _SIZE
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1 if study.modality == "CT" else 0
    if study.modality == "CT":
        ds.RescaleIntercept = 0
        ds.RescaleSlope = 1
    ds.PixelData = pixels.tobytes()
    return ds


def build_test_instances() -> list[FileDataset]:
    """Every instance of the synthetic dataset, in upload order."""
    instances: list[FileDataset] = []
    for study in _STUDIES:
        volume = _volume(study.modality)
        for series_number, description in enumerate(study.series_descriptions, start=1):
            instances.extend(
                _instance(study, series_number, description, index, volume[index])
                for index in range(_SLICES)
            )
    return instances


def encode_instance(ds: FileDataset) -> bytes:
    buffer = BytesIO()
    pydicom.dcmwrite(buffer, ds, enforce_file_format=True)
    return buffer.getvalue()


def seed_pacs_dataset() -> None:
    """Upload the synthetic dataset unless the PACS already has a SHIPILOV* study.

    Failures name the status code only: ``PACS_REST_URL`` carries credentials, so
    ``raise_for_status()`` (which prints the URL) is deliberately not used.
    """
    global _seeded
    if _seeded:
        return

    found = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Study", "Query": {"PatientName": f"{PATIENT_NAME_PREFIX}*"}},
        timeout=10,
    )
    if not found.ok:
        pytest.fail(f"Orthanc at {PACS_HOST} refused tools/find (HTTP {found.status_code})")

    if not found.json():
        for ds in build_test_instances():
            resp = requests.post(
                f"{PACS_REST_URL}/instances",
                data=encode_instance(ds),
                headers={"Content-Type": "application/dicom"},
                timeout=30,
            )
            if not resp.ok:
                pytest.fail(
                    f"Orthanc at {PACS_HOST} refused the synthetic test dataset "
                    f"(HTTP {resp.status_code})"
                )
    _seeded = True
