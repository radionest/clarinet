"""Unit tests for require_anon_or_raw helper."""

import re

import pytest

from clarinet.exceptions.domain import AnonPathError
from clarinet.models.base import DicomQueryLevel
from clarinet.utils.anon_resolve import require_anon_or_raw


def test_anon_present_returned():
    result = require_anon_or_raw(
        anon="ANON_1",
        raw="raw-id",
        level=DicomQueryLevel.PATIENT,
        fallback_to_unanonymized=False,
    )
    assert result == "ANON_1"


def test_anon_wins_over_raw_even_with_fallback():
    result = require_anon_or_raw(
        anon="ANON_1",
        raw="raw-id",
        level=DicomQueryLevel.PATIENT,
        fallback_to_unanonymized=True,
    )
    assert result == "ANON_1"


@pytest.mark.parametrize(
    ("level", "raw", "expected_msg"),
    [
        (DicomQueryLevel.PATIENT, "PAT001", "Patient has no anon_id (patient_id='PAT001')"),
        (DicomQueryLevel.STUDY, "1.2.3", "Study has no anon_uid (study_uid='1.2.3')"),
        (DicomQueryLevel.SERIES, "1.2.3.4", "Series has no anon_uid (series_uid='1.2.3.4')"),
    ],
)
def test_missing_anon_raises_in_safe_mode(level, raw, expected_msg):
    with pytest.raises(AnonPathError, match=re.escape(expected_msg)):
        require_anon_or_raw(anon=None, raw=raw, level=level, fallback_to_unanonymized=False)


@pytest.mark.parametrize(
    "level",
    [DicomQueryLevel.PATIENT, DicomQueryLevel.STUDY, DicomQueryLevel.SERIES],
)
def test_fallback_returns_raw_when_anon_missing(level):
    result = require_anon_or_raw(
        anon=None, raw="raw-value", level=level, fallback_to_unanonymized=True
    )
    assert result == "raw-value"


def test_fallback_raises_when_raw_also_missing():
    with pytest.raises(AnonPathError, match="Patient has no anon_id"):
        require_anon_or_raw(
            anon=None,
            raw=None,
            level=DicomQueryLevel.PATIENT,
            fallback_to_unanonymized=True,
        )


def test_empty_string_anon_treated_as_missing():
    with pytest.raises(AnonPathError, match="Study has no anon_uid"):
        require_anon_or_raw(
            anon="",
            raw=None,
            level=DicomQueryLevel.STUDY,
            fallback_to_unanonymized=False,
        )
