"""Unit tests for the OUTPUT path-uniqueness config-load validator."""

import pytest

from clarinet.config.path_uniqueness import validate_output_path_uniqueness
from clarinet.exceptions.domain import RecordConstraintViolationError
from tests.utils.factories import make_record_type_create, make_record_type_create_two_outputs


def test_parent_partition_without_discriminator_rejected():
    rt = make_record_type_create(
        name="second-review",
        level="SERIES",
        parent_required=True,
        unique_by={"parent"},
        output_pattern="review_{user_id}.seg.nrrd",
    )
    with pytest.raises(RecordConstraintViolationError, match="second-review"):
        validate_output_path_uniqueness(rt)


def test_parent_partition_with_parent_id_passes():
    rt = make_record_type_create(
        name="second-review",
        level="SERIES",
        parent_required=True,
        unique_by={"parent"},
        output_pattern="review_{parent_id}.seg.nrrd",
    )
    validate_output_path_uniqueness(rt)


def test_id_fast_pass():
    rt = make_record_type_create(
        name="anyid",
        level="SERIES",
        parent_required=True,
        unique_by={"parent"},
        output_pattern="out_{id}.nrrd",
    )
    validate_output_path_uniqueness(rt)


def test_none_with_multi_quota_requires_id():
    rt = make_record_type_create(
        name="m",
        level="SERIES",
        unique_by=None,
        max_records=5,
        output_pattern="out_{user_id}.nrrd",
    )
    with pytest.raises(RecordConstraintViolationError):
        validate_output_path_uniqueness(rt)


def test_origin_type_alone_rejected():
    rt = make_record_type_create(
        name="ot",
        level="SERIES",
        parent_required=True,
        unique_by={"parent"},
        output_pattern="seg_{origin_type}_{user_id}.nrrd",
    )
    with pytest.raises(RecordConstraintViolationError):
        validate_output_path_uniqueness(rt)  # two same-type parents render one filename


def test_multiple_true_exempt():
    rt = make_record_type_create(
        name="coll",
        level="SERIES",
        parent_required=True,
        unique_by={"parent"},
        output_pattern="seg_{origin_type}.nrrd",
        multiple=True,
    )
    validate_output_path_uniqueness(rt)


def test_per_file_optout_suppresses():
    rt = make_record_type_create(
        name="rep",
        level="SERIES",
        unique_by=None,
        max_records=4,
        output_pattern="report_{data.timepoint}.pdf",
        file_allow_path_collision=True,
    )
    validate_output_path_uniqueness(rt)


def test_optout_does_not_cover_siblings():
    rt = make_record_type_create_two_outputs(
        name="rep2",
        level="SERIES",
        unique_by=None,
        max_records=4,
        outputs=[("report_{data.timepoint}.pdf", True), ("summary.txt", False)],
    )
    with pytest.raises(RecordConstraintViolationError, match="summary"):
        validate_output_path_uniqueness(rt)


def test_coarser_file_level_requires_own_uid():
    """A SERIES-level type writing to a PATIENT-level file needs {series_uid} —
    otherwise every series under the same patient collides on that one path."""
    rt = make_record_type_create(
        name="patient-summary",
        level="SERIES",
        unique_by=None,
        max_records=1,
        output_pattern="summary.pdf",
        file_level="PATIENT",
    )
    with pytest.raises(RecordConstraintViolationError, match="patient-summary"):
        validate_output_path_uniqueness(rt)


def test_coarser_file_level_with_own_uid_passes():
    rt = make_record_type_create(
        name="patient-summary-ok",
        level="SERIES",
        unique_by=None,
        max_records=1,
        output_pattern="summary_{series_uid}.pdf",
        file_level="PATIENT",
    )
    validate_output_path_uniqueness(rt)
