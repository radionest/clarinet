"""Tests for clarinet.config.primitives: RecordDef.unique_by, FileRef.allow_path_collision."""

import pytest

from clarinet.config.primitives import fileref_to_file_definition
from clarinet.flow import FileDef, FileRef, RecordDef


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
