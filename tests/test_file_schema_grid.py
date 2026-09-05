"""Grid-conformance declaration fields on FileDefinition / FileDefinitionRead."""

from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from clarinet.models.file_schema import (
    FileDefinition,
    FileDefinitionRead,
    GridMismatchAction,
)


def test_action_vocabulary():
    assert set(get_args(GridMismatchAction.__value__)) == {"conform", "delete", "reject"}


def test_file_definition_defaults_to_no_declaration():
    fd = FileDefinition(name="seg", pattern="seg.nrrd")
    assert fd.grid_conform_to is None
    assert fd.on_grid_mismatch is None


def test_file_definition_carries_declaration():
    fd = FileDefinition(
        name="seg",
        pattern="seg.nrrd",
        grid_conform_to="volume",
        on_grid_mismatch="conform",
    )
    assert fd.grid_conform_to == "volume"
    assert fd.on_grid_mismatch == "conform"


def test_read_dto_carries_declaration():
    dto = FileDefinitionRead(
        name="seg",
        pattern="seg.nrrd",
        grid_conform_to="volume",
        on_grid_mismatch="delete",
    )
    assert dto.grid_conform_to == "volume"
    assert dto.on_grid_mismatch == "delete"


def test_read_dto_accepts_a_definition_object_as_the_reference():
    volume = FileDefinition(name="volume", pattern="volume.nii.gz")
    dto = FileDefinitionRead(name="seg", pattern="seg.nrrd", grid_conform_to=volume)
    assert dto.grid_conform_to == "volume"


def test_read_dto_rejects_a_reference_with_an_empty_name():
    """A ``.name``-bearing reference must not fail open on an empty name.

    ``FileDefinition`` itself cannot construct with an empty name
    (``min_length=1`` + identifier regex), so this uses a bare duck-typed
    stand-in to exercise the validator directly.
    """
    unnamed = SimpleNamespace(name="")
    with pytest.raises(ValidationError):
        FileDefinitionRead(name="seg", pattern="seg.nrrd", grid_conform_to=unnamed)


def test_read_dto_coerces_an_unknown_action_to_reject(caplog):
    """An out-of-vocabulary DB value fails closed to ``None`` (reject), not a
    pydantic ``ValidationError`` that would break every read of the owning
    RecordType."""
    with caplog.at_level("WARNING"):
        dto = FileDefinitionRead(name="seg", pattern="seg.nrrd", on_grid_mismatch="explode")
    assert dto.on_grid_mismatch is None
    assert "explode" in caplog.text


def test_columns_are_nullable():
    cols = FileDefinition.__table__.columns
    assert cols["grid_conform_to"].nullable is True
    assert cols["on_grid_mismatch"].nullable is True
