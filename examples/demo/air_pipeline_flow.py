"""Pipeline flow: Air ROI segmentation and volume calculation.

Three-step pipeline triggered on series creation:
1. segment_air — threshold HU < -900 → low_dens.seg.nrrd
2. create_air_record — create air_volume record via API
3. calculate_air_volume — compute volume, update record, finish
"""

from pathlib import Path
from typing import TYPE_CHECKING

from src.services.pipeline import Pipeline, PipelineMessage, get_broker
from src.services.recordflow import series

if TYPE_CHECKING:
    from src.client import ClarinetClient

broker = get_broker()


async def _get_client() -> ClarinetClient:
    """Create an authenticated ClarinetClient with admin credentials."""
    from src.client import ClarinetClient
    from src.settings import settings

    client = ClarinetClient(
        f"http://{settings.host}:{settings.port}/api",
        username=settings.admin_email,
        password=settings.admin_password,
    )
    await client.login()
    return client


@broker.task()
async def segment_air(msg: dict) -> dict:
    """Threshold segmentation (HU < -900) producing low_dens.seg.nrrd."""
    import nrrd
    import numpy as np

    message = PipelineMessage(**msg)

    client = await _get_client()
    try:
        series_read = await client.get_series(message.series_uid)
        working_dir = series_read.working_folder
    finally:
        await client.close()

    if not working_dir:
        raise ValueError(f"No working folder for series {message.series_uid}")

    working_path = Path(working_dir)
    working_path.mkdir(parents=True, exist_ok=True)

    # Fetch DICOM from PACS via C-GET (skip if files already present)
    dcm_files = list(working_path.glob("*.dcm"))
    if not dcm_files:
        from src.services.dicom import DicomClient, DicomNode
        from src.settings import settings

        dicom_client = DicomClient(calling_aet=settings.dicom_aet, max_pdu=settings.dicom_max_pdu)
        pacs = DicomNode(
            aet=settings.pacs_aet,
            host=settings.pacs_host,
            port=settings.pacs_port,
        )
        result = await dicom_client.get_series(
            study_uid=series_read.study.study_uid,
            series_uid=series_read.series_uid,
            peer=pacs,
            output_dir=working_path,
        )
        if result.num_completed == 0:
            raise FileNotFoundError(f"No DICOM instances retrieved for series {message.series_uid}")
        dcm_files = list(working_path.glob("*.dcm"))

    # Read DICOM slices into a numpy volume
    import pydicom

    slices = [pydicom.dcmread(f) for f in dcm_files]
    slices.sort(key=lambda s: float(getattr(s, "InstanceNumber", 0)))

    first = slices[0]
    slope = float(getattr(first, "RescaleSlope", 1))
    intercept = float(getattr(first, "RescaleIntercept", 0))
    data = np.stack([s.pixel_array for s in slices], axis=-1).astype(np.float32)
    data = data * slope + intercept

    # Extract spatial metadata for NRRD header
    pixel_spacing = [float(x) for x in first.PixelSpacing]
    slice_thickness = float(first.SliceThickness)
    position = [float(x) for x in first.ImagePositionPatient]
    orientation = [float(x) for x in first.ImageOrientationPatient]
    row_dir = orientation[:3]
    col_dir = orientation[3:]

    mask = (data < -900).astype(np.uint8)

    seg_header = {
        "type": "unsigned char",
        "dimension": 3,
        "space": "left-posterior-superior",
        "sizes": list(mask.shape),
        "space directions": [
            [row_dir[i] * pixel_spacing[0] for i in range(3)],
            [col_dir[i] * pixel_spacing[1] for i in range(3)],
            [0.0, 0.0, slice_thickness],
        ],
        "space origin": position,
        "kinds": ["domain", "domain", "domain"],
        "encoding": "gzip",
    }

    seg_path = working_path / "low_dens.seg.nrrd"
    nrrd.write(str(seg_path), mask, seg_header)

    message.payload["working_dir"] = str(working_path)
    return message.model_dump()


@broker.task()
async def create_air_record(msg: dict) -> dict:
    """Create an air_volume record via the Clarinet API."""
    from src.models import RecordCreate

    message = PipelineMessage(**msg)

    client = await _get_client()
    try:
        record = await client.create_record(
            RecordCreate(
                record_type_name="air_volume",
                patient_id=message.patient_id,
                study_uid=message.study_uid,
                series_uid=message.series_uid,
                user_id=None,
                context_info="Auto-created by air_analysis pipeline",
            )
        )
    finally:
        await client.close()

    message.payload["record_id"] = record.id
    return message.model_dump()


@broker.task()
async def calculate_air_volume(msg: dict) -> dict:
    """Calculate air volume from segmentation and update the record."""
    import nrrd
    import numpy as np

    from src.models import RecordStatus

    message = PipelineMessage(**msg)

    working_dir = message.payload.get("working_dir")
    record_id = message.payload.get("record_id")
    if not working_dir or not record_id:
        raise ValueError("Missing working_dir or record_id in payload")

    seg_path = Path(working_dir) / "low_dens.seg.nrrd"
    data, header = nrrd.read(str(seg_path))

    voxel_count = int(np.count_nonzero(data))
    space_dirs = np.array(header["space directions"])
    voxel_volume = float(abs(np.linalg.det(space_dirs)))

    volume_mm3 = voxel_count * voxel_volume
    volume_ml = volume_mm3 / 1000.0

    client = await _get_client()
    try:
        await client.update_record_data(
            record_id,
            {
                "volume_mm3": round(volume_mm3, 2),
                "volume_ml": round(volume_ml, 4),
                "voxel_count": voxel_count,
                "threshold_hu": -900,
            },
        )
        await client.update_record_status(record_id, RecordStatus.finished)
    finally:
        await client.close()

    return message.model_dump()


# Pipeline chain: 3-step air analysis
air_analysis = (
    Pipeline("air_analysis").step(segment_air).step(create_air_record).step(calculate_air_volume)
)

# Trigger pipeline when a new series is created
series().on_created().pipeline("air_analysis")
