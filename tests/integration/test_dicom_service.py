"""Integration tests for DICOM service against a live Orthanc PACS server.

These tests require a running Orthanc instance at PACS_HOST:PACS_PORT
with known test data pre-loaded. They are skipped automatically if the
server is unreachable.

Run:
    uv run pytest tests/integration/test_dicom_service.py -v
    uv run pytest -m dicom -v
    uv run pytest -m "not dicom"   # exclude from CI
"""

from pathlib import Path

import pydicom
import pytest
import requests

from clarinet.services.dicom import (
    DicomClient,
    DicomNode,
    ImageQuery,
    SeriesQuery,
    StudyQuery,
    StudyResult,
)
from clarinet.services.dicom.models import SeriesResult
from clarinet.settings import settings
from tests.config import CALLING_AET, PACS_AET, PACS_HOST, PACS_PORT, PACS_REST_URL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_anonymized_copy(patient_id: str | None) -> bool:
    """True for a study that is a transient anonymized copy on the shared PACS.

    The anonymize -> send-to-PACS tests C-STORE copies whose PatientID carries
    ``settings.anon_id_prefix`` (e.g. ``CLARINET_42``) back into the shared test
    Orthanc. Those studies are created and deleted mid-session and Orthanc
    indexes them asynchronously, so whole-PACS count assertions must exclude
    them: the remaining (non-anonymized) studies are immutable for the run, so a
    REST snapshot and the session-cached C-FIND always agree on them regardless
    of when each was taken.
    """
    return bool(patient_id) and str(patient_id).startswith(f"{settings.anon_id_prefix}_")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pacs_available() -> None:
    """Skip the entire session if the PACS server is unreachable."""
    try:
        resp = requests.get(f"{PACS_REST_URL}/system", timeout=2)
        resp.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
        pytest.skip("Orthanc PACS server is not reachable — skipping DICOM tests")


@pytest.fixture(scope="session")
def orthanc_expected_counts(pacs_available: None) -> dict[str, int]:
    """Ground-truth study counts from a single Orthanc REST snapshot.

    Every count excludes anonymized copies (see :func:`_is_anonymized_copy`) so
    the whole-PACS comparisons in this module stay deterministic against the
    shared mutable test PACS. All counts are derived from one expanded
    ``/tools/find`` response, so they can never drift against each other.
    """
    resp = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={
            "Level": "Study",
            "Query": {},
            "Expand": True,
            "RequestedTags": ["ModalitiesInStudy"],
        },
        timeout=5,
    )
    resp.raise_for_status()
    studies = resp.json()
    stable = [
        s
        for s in studies
        if not _is_anonymized_copy(s.get("PatientMainDicomTags", {}).get("PatientID"))
    ]

    return {
        "total_studies": len(stable),
        "shipilov_studies": sum(
            1
            for s in stable
            if str(s.get("PatientMainDicomTags", {}).get("PatientName") or "")
            .upper()
            .startswith("SHIPILOV")
        ),
        "ct_studies": sum(
            1
            for s in stable
            if "CT" in (s.get("RequestedTags", {}).get("ModalitiesInStudy") or "").split("\\")
        ),
        "mr_studies": sum(
            1
            for s in stable
            if "MR" in (s.get("RequestedTags", {}).get("ModalitiesInStudy") or "").split("\\")
        ),
    }


@pytest.fixture(scope="session")
def orthanc_node(pacs_available: None) -> DicomNode:
    """Pre-configured DicomNode pointing at the test Orthanc."""
    return DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)


@pytest.fixture(scope="session")
def dicom_client() -> DicomClient:
    """Shared stateless DicomClient instance."""
    return DicomClient(calling_aet=CALLING_AET)


@pytest.fixture(scope="session")
def all_studies(dicom_client: DicomClient, orthanc_node: DicomNode) -> list[StudyResult]:
    """Cached list of all studies on the PACS (fetched once per session)."""
    import asyncio

    return asyncio.run(dicom_client.find_studies(StudyQuery(), orthanc_node))


@pytest.fixture(scope="session")
def mr_study(all_studies: list[StudyResult]) -> StudyResult:
    """Smallest SHIPILOV MR study on the PACS — used for C-GET tests.

    Restricted to the SHIPILOV test patient: the anonymize -> send-to-PACS
    tests push anonymized MR copies (patient ``CLARINET_*``) back to the shared
    PACS, so an unscoped "smallest MR study" would intermittently select one
    while it is still being ingested, yielding flaky instance-count mismatches.
    The SHIPILOV studies are immutable for the test run, so this selection is
    stable. Picks the fewest-instance study to avoid C-GET timeouts on large
    studies (2000+ instances).
    """
    matches = [
        s
        for s in all_studies
        if s.modalities_in_study
        and "MR" in s.modalities_in_study
        and s.patient_name
        and "SHIPILOV" in str(s.patient_name).upper()
    ]
    assert matches, "No SHIPILOV MR study found on test PACS"
    return min(matches, key=lambda s: s.number_of_study_related_instances or float("inf"))


@pytest.fixture(scope="session")
def mr_study_instance_count(pacs_available: None, mr_study: StudyResult) -> int:
    """Instance count for selected MR study from Orthanc REST API."""
    orthanc_ids = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Study", "Query": {"StudyInstanceUID": mr_study.study_instance_uid}},
        timeout=5,
    ).json()
    stats = requests.get(f"{PACS_REST_URL}/studies/{orthanc_ids[0]}/statistics", timeout=5).json()
    return int(stats["CountInstances"])


@pytest.fixture(scope="session")
def mr_series_list(
    dicom_client: DicomClient, orthanc_node: DicomNode, mr_study: StudyResult
) -> list[SeriesResult]:
    """Cached series list for the MR study."""
    import asyncio

    query = SeriesQuery(study_instance_uid=mr_study.study_instance_uid)
    return asyncio.run(dicom_client.find_series(query, orthanc_node))


# ===========================================================================
# A. C-FIND Studies
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_all_studies(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    all_studies: list[StudyResult],
    orthanc_expected_counts: dict[str, int],
) -> None:
    """Non-anonymized studies match the REST count; every study has a UID."""
    stable_studies = [s for s in all_studies if not _is_anonymized_copy(s.patient_id)]
    assert len(stable_studies) == orthanc_expected_counts["total_studies"]
    for study in all_studies:
        assert study.study_instance_uid


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_studies_by_patient_name(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    orthanc_expected_counts: dict[str, int],
) -> None:
    """Wildcard search on patient name returns matching results."""
    results = await dicom_client.find_studies(StudyQuery(patient_name="SHIPILOV*"), orthanc_node)
    # No _is_anonymized_copy filter needed here: anonymization rewrites PatientName
    # to CLARINET_*, so the SHIPILOV* query can never match an anonymized copy —
    # results already align with the anon-excluded shipilov_studies count.
    assert len(results) == orthanc_expected_counts["shipilov_studies"]
    assert results[0].modalities_in_study and "MR" in results[0].modalities_in_study


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_studies_by_modality_ct(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    orthanc_expected_counts: dict[str, int],
) -> None:
    """Filtering by CT returns the expected number of non-anonymized studies."""
    results = await dicom_client.find_studies(StudyQuery(modality="CT"), orthanc_node)
    stable = [s for s in results if not _is_anonymized_copy(s.patient_id)]
    assert len(stable) == orthanc_expected_counts["ct_studies"]


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_studies_by_modality_mr(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    orthanc_expected_counts: dict[str, int],
) -> None:
    """Filtering by MR returns the expected number of non-anonymized studies."""
    results = await dicom_client.find_studies(StudyQuery(modality="MR"), orthanc_node)
    stable = [s for s in results if not _is_anonymized_copy(s.patient_id)]
    assert len(stable) == orthanc_expected_counts["mr_studies"]


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_studies_nonexistent_patient(
    dicom_client: DicomClient, orthanc_node: DicomNode
) -> None:
    """Querying for a non-existent patient returns an empty list."""
    results = await dicom_client.find_studies(
        StudyQuery(patient_name="DOESNOTEXIST_999"), orthanc_node
    )
    assert results == []


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_studies_by_patient_id(
    dicom_client: DicomClient, orthanc_node: DicomNode, mr_study: StudyResult
) -> None:
    """Query by patient_id returns the expected study."""
    assert mr_study.patient_id, "MR study has no patient_id"
    results = await dicom_client.find_studies(
        StudyQuery(patient_id=mr_study.patient_id), orthanc_node
    )
    assert len(results) >= 1
    uids = {s.study_instance_uid for s in results}
    assert mr_study.study_instance_uid in uids


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_studies_by_study_uid(
    dicom_client: DicomClient, orthanc_node: DicomNode, mr_study: StudyResult
) -> None:
    """Query by study_instance_uid returns exactly that study."""
    results = await dicom_client.find_studies(
        StudyQuery(study_instance_uid=mr_study.study_instance_uid), orthanc_node
    )
    assert len(results) == 1
    assert results[0].study_instance_uid == mr_study.study_instance_uid


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_studies_fields_populated(
    dicom_client: DicomClient, orthanc_node: DicomNode, mr_study: StudyResult
) -> None:
    """Verify study metadata fields are populated for the MR study."""
    results = await dicom_client.find_studies(
        StudyQuery(study_instance_uid=mr_study.study_instance_uid), orthanc_node
    )
    assert len(results) == 1
    study = results[0]
    assert study.study_date is not None
    assert study.study_time is not None
    assert study.number_of_study_related_series is not None
    assert study.number_of_study_related_instances is not None


# ===========================================================================
# B. C-FIND Series
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_series_for_mr_study(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
    mr_study_instance_count: int,
) -> None:
    """MR study has >= 1 series, all MR, totalling expected instances."""
    assert len(mr_series_list) >= 1
    for series in mr_series_list:
        assert series.modality == "MR"
    total_instances = sum(s.number_of_series_related_instances or 0 for s in mr_series_list)
    assert total_instances == mr_study_instance_count


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_series_filter_by_modality(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    all_studies: list[StudyResult],
) -> None:
    """CT study filtered by modality='CT' returns only CT series."""
    ct_studies = [s for s in all_studies if s.modalities_in_study and "CT" in s.modalities_in_study]
    assert ct_studies, "No CT study found"
    ct_study = ct_studies[0]

    series = await dicom_client.find_series(
        SeriesQuery(study_instance_uid=ct_study.study_instance_uid, modality="CT"),
        orthanc_node,
    )
    assert series
    for s in series:
        assert s.modality == "CT"


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_series_nonexistent_study(
    dicom_client: DicomClient, orthanc_node: DicomNode
) -> None:
    """Querying series for a fake study UID returns an empty list."""
    results = await dicom_client.find_series(
        SeriesQuery(study_instance_uid="1.2.3.4.5.6.7.8.9.FAKE"),
        orthanc_node,
    )
    assert results == []


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_series_by_series_uid(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
) -> None:
    """Query by specific series_instance_uid returns exactly one series."""
    target = mr_series_list[0]
    results = await dicom_client.find_series(
        SeriesQuery(
            study_instance_uid=mr_study.study_instance_uid,
            series_instance_uid=target.series_instance_uid,
        ),
        orthanc_node,
    )
    assert len(results) == 1
    assert results[0].series_instance_uid == target.series_instance_uid


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_series_count_matches_study(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
) -> None:
    """Series count from C-FIND matches study.number_of_study_related_series."""
    assert mr_study.number_of_study_related_series is not None
    assert len(mr_series_list) == mr_study.number_of_study_related_series


# ===========================================================================
# C. C-FIND Images
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_images_for_series(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
) -> None:
    """Image count matches series number_of_series_related_instances."""
    series = mr_series_list[0]
    images = await dicom_client.find_images(
        ImageQuery(
            study_instance_uid=mr_study.study_instance_uid,
            series_instance_uid=series.series_instance_uid,
        ),
        orthanc_node,
    )
    assert len(images) == (series.number_of_series_related_instances or 0)


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_images_fields_populated(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
) -> None:
    """Each image result has correct study/series UIDs and non-None sop_class_uid."""
    series = mr_series_list[0]
    images = await dicom_client.find_images(
        ImageQuery(
            study_instance_uid=mr_study.study_instance_uid,
            series_instance_uid=series.series_instance_uid,
        ),
        orthanc_node,
    )
    assert images
    for img in images:
        assert img.study_instance_uid == mr_study.study_instance_uid
        assert img.series_instance_uid == series.series_instance_uid
        assert img.sop_class_uid is not None


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_images_specific_instance(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
) -> None:
    """Querying by a known sop_instance_uid returns exactly 1 result."""
    series = mr_series_list[0]
    all_images = await dicom_client.find_images(
        ImageQuery(
            study_instance_uid=mr_study.study_instance_uid,
            series_instance_uid=series.series_instance_uid,
        ),
        orthanc_node,
    )
    assert all_images

    target_uid = all_images[0].sop_instance_uid
    filtered = await dicom_client.find_images(
        ImageQuery(
            study_instance_uid=mr_study.study_instance_uid,
            series_instance_uid=series.series_instance_uid,
            sop_instance_uid=target_uid,
        ),
        orthanc_node,
    )
    assert len(filtered) == 1
    assert filtered[0].sop_instance_uid == target_uid


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_images_nonexistent_series(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
) -> None:
    """Querying images for a fake series UID returns an empty list."""
    results = await dicom_client.find_images(
        ImageQuery(
            study_instance_uid=mr_study.study_instance_uid,
            series_instance_uid="1.2.3.4.5.6.7.8.9.FAKE_SERIES",
        ),
        orthanc_node,
    )
    assert results == []


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_images_rows_columns(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
) -> None:
    """MR image results have rows and columns populated (pixel data present)."""
    series = mr_series_list[0]
    images = await dicom_client.find_images(
        ImageQuery(
            study_instance_uid=mr_study.study_instance_uid,
            series_instance_uid=series.series_instance_uid,
        ),
        orthanc_node,
    )
    assert images
    for img in images:
        assert img.rows is not None and img.rows > 0
        assert img.columns is not None and img.columns > 0


# ===========================================================================
# D. C-GET to Disk
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_study_to_disk(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    tmp_path: Path,
    mr_study_instance_count: int,
) -> None:
    """C-GET study to disk: success, expected completed, 0 failed, matching .dcm files."""
    result = await dicom_client.get_study(
        study_uid=mr_study.study_instance_uid,
        peer=orthanc_node,
        output_dir=tmp_path,
    )
    assert result.status == "success"
    assert result.num_completed == mr_study_instance_count
    assert result.num_failed == 0

    dcm_files = list(tmp_path.glob("*.dcm"))
    assert len(dcm_files) == mr_study_instance_count


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_series_to_disk(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
    tmp_path: Path,
) -> None:
    """C-GET series to disk: success, .dcm count matches num_completed."""
    series = mr_series_list[0]
    result = await dicom_client.get_series(
        study_uid=mr_study.study_instance_uid,
        series_uid=series.series_instance_uid,
        peer=orthanc_node,
        output_dir=tmp_path,
    )
    assert result.status == "success"
    assert result.num_completed > 0

    dcm_files = list(tmp_path.glob("*.dcm"))
    assert len(dcm_files) == result.num_completed


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_study_to_disk_valid_dicom(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    tmp_path: Path,
) -> None:
    """A retrieved .dcm file is valid DICOM with PatientName and Modality=='MR'."""
    await dicom_client.get_study(
        study_uid=mr_study.study_instance_uid,
        peer=orthanc_node,
        output_dir=tmp_path,
    )
    dcm_files = list(tmp_path.glob("*.dcm"))
    assert dcm_files

    ds = pydicom.dcmread(dcm_files[0])
    assert hasattr(ds, "PatientName")
    assert ds.Modality == "MR"


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_study_with_patient_id(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    tmp_path: Path,
    mr_study_instance_count: int,
) -> None:
    """C-GET study with patient_id param succeeds and returns expected files."""
    assert mr_study.patient_id, "MR study has no patient_id"
    result = await dicom_client.get_study(
        study_uid=mr_study.study_instance_uid,
        peer=orthanc_node,
        output_dir=tmp_path,
        patient_id=mr_study.patient_id,
    )
    assert result.status == "success"
    assert result.num_completed == mr_study_instance_count
    assert result.num_failed == 0


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_series_to_disk_valid_dicom(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
    tmp_path: Path,
) -> None:
    """Series-level C-GET produces valid DICOM files with correct Modality."""
    series = mr_series_list[0]
    await dicom_client.get_series(
        study_uid=mr_study.study_instance_uid,
        series_uid=series.series_instance_uid,
        peer=orthanc_node,
        output_dir=tmp_path,
    )
    dcm_files = list(tmp_path.glob("*.dcm"))
    assert dcm_files

    ds = pydicom.dcmread(dcm_files[0])
    assert hasattr(ds, "PatientName")
    assert ds.Modality == "MR"


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_series_instance_count_matches_find(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
    tmp_path: Path,
) -> None:
    """C-GET series num_completed matches C-FIND number_of_series_related_instances."""
    series = mr_series_list[0]
    result = await dicom_client.get_series(
        study_uid=mr_study.study_instance_uid,
        series_uid=series.series_instance_uid,
        peer=orthanc_node,
        output_dir=tmp_path,
    )
    assert result.num_completed == (series.number_of_series_related_instances or 0)


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_study_to_disk_file_uids_unique(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    tmp_path: Path,
    mr_study_instance_count: int,
) -> None:
    """All .dcm files have unique SOPInstanceUID — no overwrites."""
    await dicom_client.get_study(
        study_uid=mr_study.study_instance_uid,
        peer=orthanc_node,
        output_dir=tmp_path,
    )
    dcm_files = list(tmp_path.glob("*.dcm"))
    assert len(dcm_files) == mr_study_instance_count

    uids = {str(pydicom.dcmread(f).SOPInstanceUID) for f in dcm_files}
    assert len(uids) == mr_study_instance_count


# ===========================================================================
# E. C-GET to Memory
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_study_to_memory(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_study_instance_count: int,
) -> None:
    """C-GET study to memory: success, expected completed and instance count."""
    result = await dicom_client.get_study_to_memory(
        study_uid=mr_study.study_instance_uid,
        peer=orthanc_node,
    )
    assert result.status == "success"
    assert result.num_completed == mr_study_instance_count
    assert len(result.instances) == mr_study_instance_count


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_study_to_memory_are_datasets(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
) -> None:
    """Each in-memory instance is a pydicom.Dataset with SOPInstanceUID."""
    result = await dicom_client.get_study_to_memory(
        study_uid=mr_study.study_instance_uid,
        peer=orthanc_node,
    )
    for instance in result.instances.values():
        assert isinstance(instance, pydicom.Dataset)
        assert hasattr(instance, "SOPInstanceUID")


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_study_to_memory_matches_find(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
) -> None:
    """Set of SOPInstanceUIDs from C-GET equals set from C-FIND."""
    # Collect all image UIDs via C-FIND
    find_uids: set[str] = set()
    for series in mr_series_list:
        images = await dicom_client.find_images(
            ImageQuery(
                study_instance_uid=mr_study.study_instance_uid,
                series_instance_uid=series.series_instance_uid,
            ),
            orthanc_node,
        )
        find_uids.update(img.sop_instance_uid for img in images)

    # Collect all instance UIDs via C-GET to memory
    result = await dicom_client.get_study_to_memory(
        study_uid=mr_study.study_instance_uid,
        peer=orthanc_node,
    )
    get_uids = set(result.instances.keys())

    assert find_uids == get_uids


# ===========================================================================
# F. C-MOVE
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_move_study_unknown_destination(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
) -> None:
    """C-MOVE to a non-existent AET results in failures."""
    result = await dicom_client.move_study(
        study_uid=mr_study.study_instance_uid,
        peer=orthanc_node,
        destination_aet="NONEXISTENT",
    )
    assert result.num_failed > 0 or result.status != "success"


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_move_series_unknown_destination(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
) -> None:
    """C-MOVE series to a non-existent AET results in failures."""
    series = mr_series_list[0]
    result = await dicom_client.move_series(
        study_uid=mr_study.study_instance_uid,
        series_uid=series.series_instance_uid,
        peer=orthanc_node,
        destination_aet="NONEXISTENT",
    )
    assert result.num_failed > 0 or result.status != "success"


# ===========================================================================
# G. Error Handling (no pacs_available dependency — use fake hosts)
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_studies_unreachable_peer() -> None:
    """C-FIND against an unreachable host raises HTTPException(409)."""
    from fastapi import HTTPException

    client = DicomClient(calling_aet=CALLING_AET)
    fake_node = DicomNode(aet="FAKE", host="192.168.122.254", port=9999)

    with pytest.raises(HTTPException) as exc_info:
        await client.find_studies(StudyQuery(), fake_node, timeout=3)
    assert exc_info.value.status_code == 409


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_get_study_unreachable_peer(tmp_path: Path) -> None:
    """C-GET against an unreachable host raises HTTPException(409)."""
    from fastapi import HTTPException

    client = DicomClient(calling_aet=CALLING_AET)
    fake_node = DicomNode(aet="FAKE", host="192.168.122.254", port=9999)

    with pytest.raises(HTTPException) as exc_info:
        await client.get_study(
            study_uid="1.2.3.FAKE",
            peer=fake_node,
            output_dir=tmp_path,
            timeout=3,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_move_study_unreachable_peer(tmp_path: Path) -> None:
    """C-MOVE study to an unreachable host raises HTTPException(409)."""
    from fastapi import HTTPException

    client = DicomClient(calling_aet=CALLING_AET)
    fake_node = DicomNode(aet="FAKE", host="192.168.122.254", port=9999)

    with pytest.raises(HTTPException) as exc_info:
        await client.move_study(
            study_uid="1.2.3.FAKE",
            peer=fake_node,
            destination_aet="ANYWHERE",
            timeout=3,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_move_series_unreachable_peer(tmp_path: Path) -> None:
    """C-MOVE series to an unreachable host raises HTTPException(409)."""
    from fastapi import HTTPException

    client = DicomClient(calling_aet=CALLING_AET)
    fake_node = DicomNode(aet="FAKE", host="192.168.122.254", port=9999)

    with pytest.raises(HTTPException) as exc_info:
        await client.move_series(
            study_uid="1.2.3.FAKE",
            series_uid="1.2.3.4.FAKE",
            peer=fake_node,
            destination_aet="ANYWHERE",
            timeout=3,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_find_studies_wrong_aet(pacs_available: None) -> None:
    """C-FIND with the wrong called AET against a real host.

    Orthanc is provisioned with DicomCheckCalledAet=false, so the
    association succeeds regardless of called AET.
    """
    client = DicomClient(calling_aet=CALLING_AET)
    node = DicomNode(aet="WRONG_AET", host=PACS_HOST, port=PACS_PORT)

    results = await client.find_studies(StudyQuery(), node, timeout=5)
    assert isinstance(results, list)


# ===========================================================================
# H. Cross-validation
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_study_instance_count_matches_series_sum(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    mr_study: StudyResult,
    mr_series_list: list[SeriesResult],
) -> None:
    """study.number_of_study_related_instances == sum of each series instance count."""
    assert mr_study.number_of_study_related_instances is not None
    series_sum = sum(s.number_of_series_related_instances or 0 for s in mr_series_list)
    assert mr_study.number_of_study_related_instances == series_sum
