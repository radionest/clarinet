"""The DICOM suite carries its own data: a synthetic dataset seeded into the test PACS.

The suite used to depend on a ``SHIPILOV*`` patient that existed only on one
developer's Orthanc. Everywhere else the fixtures died with "No SHIPILOV studies
found on test PACS". These tests pin the properties the DICOM tests rely on, so
the generator cannot drift away from them unnoticed.
"""

from collections import defaultdict
from io import BytesIO
from itertools import pairwise
from unittest.mock import MagicMock, patch

import pydicom
import pytest
import requests

from clarinet.models.patient import PATIENT_ID_PATTERN
from tests.utils.pacs_dataset import build_test_instances, encode_instance, seed_pacs_dataset


def _response(status_code: int, payload: object = None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.json.return_value = payload
    return resp


def _by_study() -> dict[str, list[pydicom.Dataset]]:
    studies: dict[str, list[pydicom.Dataset]] = defaultdict(list)
    for ds in build_test_instances():
        studies[ds.StudyInstanceUID].append(ds)
    return studies


def test_the_shipilov_patient_has_exactly_one_study_and_it_is_mr() -> None:
    """Fixtures take ``results[0]`` of an unordered SHIPILOV* query and assert MR."""
    shipilov = {
        uid: instances
        for uid, instances in _by_study().items()
        if str(instances[0].PatientName).upper().startswith("SHIPILOV")
    }
    assert len(shipilov) == 1
    (instances,) = shipilov.values()
    assert {ds.Modality for ds in instances} == {"MR"}


def test_a_ct_study_exists_under_another_patient() -> None:
    """``test_find_series_filter_by_modality`` needs CT; preload needs two studies."""
    studies = _by_study()
    assert len(studies) >= 2
    ct = [i for i in studies.values() if i[0].Modality == "CT"]
    assert ct
    assert not str(ct[0][0].PatientName).upper().startswith("SHIPILOV")


def test_patient_ids_are_importable_and_never_look_anonymized() -> None:
    for ds in build_test_instances():
        assert PATIENT_ID_PATTERN.match(ds.PatientID)
        # The count fixtures drop PatientIDs with this prefix as anonymized copies.
        assert not ds.PatientID.startswith("CLARINET_")


def test_uids_are_deterministic_and_unique() -> None:
    """Re-seeding must overwrite, not duplicate — two xdist groups may seed at once."""
    first = [ds.SOPInstanceUID for ds in build_test_instances()]
    second = [ds.SOPInstanceUID for ds in build_test_instances()]
    assert first == second
    assert len(set(first)) == len(first)


def test_every_series_is_a_loadable_volume() -> None:
    """Slicer loads these and WADO-RS serves their frames, so geometry has to be real."""
    series: dict[str, list[pydicom.Dataset]] = defaultdict(list)
    for ds in build_test_instances():
        series[ds.SeriesInstanceUID].append(ds)

    for instances in series.values():
        assert len(instances) >= 3
        assert len({(ds.Rows, ds.Columns) for ds in instances}) == 1
        z = [float(ds.ImagePositionPatient[2]) for ds in instances]
        gaps = {round(b - a, 6) for a, b in pairwise(z)}
        assert len(gaps) == 1 and gaps.pop() > 0
        for ds in instances:
            assert len(ds.PixelData) == ds.Rows * ds.Columns * ds.BitsAllocated // 8
            assert ds.StudyDate and ds.StudyTime
            assert ds.SeriesDescription


def test_instances_encode_as_part10_files() -> None:
    ds = build_test_instances()[0]
    decoded = pydicom.dcmread(BytesIO(encode_instance(ds)))
    assert decoded.SOPInstanceUID == ds.SOPInstanceUID
    assert decoded.file_meta.MediaStorageSOPClassUID == ds.SOPClassUID


def test_seeding_leaves_a_pacs_that_already_has_the_dataset_alone() -> None:
    """A PACS holding real SHIPILOV studies must never be written to."""
    with (
        patch("tests.utils.pacs_dataset._seeded", False),
        patch("tests.utils.pacs_dataset.requests.post") as post,
    ):
        post.return_value = _response(200, ["some-orthanc-id"])
        seed_pacs_dataset()
    assert post.call_count == 1
    assert post.call_args.args[0].endswith("/tools/find")


def test_seeding_uploads_every_instance_when_the_dataset_is_missing() -> None:
    with (
        patch("tests.utils.pacs_dataset._seeded", False),
        patch("tests.utils.pacs_dataset.requests.post") as post,
    ):
        post.side_effect = lambda url, **_: _response(200, [] if url.endswith("/find") else {})
        seed_pacs_dataset()

    uploads = [c for c in post.call_args_list if c.args[0].endswith("/instances")]
    assert len(uploads) == len(build_test_instances())
    assert uploads[0].kwargs["headers"] == {"Content-Type": "application/dicom"}


def test_a_refused_upload_fails_without_printing_the_credentialed_url() -> None:
    with (
        patch("tests.utils.pacs_dataset._seeded", False),
        patch("tests.utils.pacs_dataset.requests.post") as post,
        pytest.raises(pytest.fail.Exception, match="HTTP 403") as excinfo,
    ):
        post.side_effect = lambda url, **_: (
            _response(200, []) if url.endswith("/find") else _response(403)
        )
        seed_pacs_dataset()
    assert "@" not in str(excinfo.value)
