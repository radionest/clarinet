"""Tests for clarinet.config.primitives: RecordDef.unique_by, FileRef.allow_path_collision."""

import pytest
from pydantic import ValidationError

from clarinet.config.primitives import fileref_to_file_definition
from clarinet.flow import FileDef, FileRef, RecordDef
from clarinet.models.base import DicomQueryLevel, ViewerMode
from clarinet.models.uniqueness import DEFAULT_UNIQUE_BY, legacy_unique_per_user


def test_unique_per_user_true_maps_to_user():
    with pytest.warns(DeprecationWarning):
        rd = RecordDef(name="a", unique_per_user=True)
    assert rd.unique_by == frozenset({"user"})


def test_unique_per_user_false_maps_to_none():
    with pytest.warns(DeprecationWarning):
        rd = RecordDef(name="b", unique_per_user=False)
    assert rd.unique_by is None


def test_default_is_user_parent():
    assert RecordDef(name="c").unique_by == frozenset({"user", "parent"})


def test_explicit_unique_by_wins_over_flag():
    with pytest.warns(DeprecationWarning):
        rd = RecordDef(name="d", unique_by={"parent"}, unique_per_user=True)
    assert rd.unique_by == frozenset({"parent"})


def test_allow_path_collision_survives_conversion():
    file_def = FileDef(name="out_file", pattern="out.nrrd", level="SERIES")
    ref = FileRef(file_def, "output", allow_path_collision=True)
    assert ref.allow_path_collision is True

    file_definition = fileref_to_file_definition(ref)
    assert file_definition.allow_path_collision is True


def test_allow_path_collision_defaults_false():
    file_def = FileDef(name="out_file", pattern="out.nrrd", level="SERIES")
    ref = FileRef(file_def, "output")
    assert ref.allow_path_collision is False
    assert fileref_to_file_definition(ref).allow_path_collision is False


def test_filedef_accepts_string_level_and_normalizes() -> None:
    f = FileDef(pattern="{study_uid}/mask.nrrd", level="STUDY")
    assert f.level is DicomQueryLevel.STUDY


def test_filedef_accepts_lowercase_string_level() -> None:
    # _coerce_dicom_level upper()s its input, so this is valid at runtime.
    f = FileDef(pattern="{study_uid}/mask.nrrd", level="study")
    assert f.level is DicomQueryLevel.STUDY


def test_filedef_string_and_enum_dump_identically() -> None:
    from_str = FileDef(pattern="p", level="SERIES")
    from_enum = FileDef(pattern="p", level=DicomQueryLevel.SERIES)
    assert from_str.model_dump() == from_enum.model_dump()


def test_filedef_rejects_unknown_level() -> None:
    with pytest.raises(ValidationError):
        FileDef(pattern="p", level="THIGH")


def test_filedef_level_field_annotation_unchanged() -> None:
    # The pydantic contract must not move; only __init__ widens.
    assert FileDef.model_fields["level"].annotation is DicomQueryLevel


def test_filedef_requires_level_when_omitted() -> None:
    # The __init__ sentinel must not forward a default in place of "not passed":
    # FileDef.level has no pydantic default, so omitting it must still raise
    # pydantic's own "field required" error rather than silently defaulting to
    # some placeholder level.
    with pytest.raises(ValidationError):
        FileDef(pattern="p")


def test_recorddef_omitted_fields_keep_pydantic_defaults() -> None:
    # The sentinel must not leak: omitted params must not be forwarded at all.
    r = RecordDef(name="first-check")
    assert r.level is DicomQueryLevel.SERIES
    assert r.viewer_mode is ViewerMode.SINGLE_SERIES
    assert r.unique_by == frozenset(DEFAULT_UNIQUE_BY)


def test_recorddef_unique_by_none_means_no_uniqueness() -> None:
    # None is a MEANINGFUL value here, distinct from "not passed".
    r = RecordDef(name="first-check", unique_by=None)
    assert r.unique_by is None


def test_recorddef_unique_by_false_is_toml_off() -> None:
    r = RecordDef(name="first-check", unique_by=False)
    assert r.unique_by is None


def test_recorddef_string_viewer_mode() -> None:
    r = RecordDef(name="first-check", viewer_mode="all_series")
    assert r.viewer_mode is ViewerMode.ALL_SERIES


def test_recorddef_unique_per_user_still_warns_and_maps() -> None:
    with pytest.warns(DeprecationWarning):
        r = RecordDef(name="first-check", unique_per_user=True)
    assert r.unique_by == legacy_unique_per_user(True)


def test_recorddef_explicit_unique_by_beats_unique_per_user() -> None:
    with pytest.warns(DeprecationWarning):
        r = RecordDef(name="first-check", unique_per_user=True, unique_by=["parent"])
    assert r.unique_by == frozenset({"parent"})
