"""Config-load rejection of invalid grid-conformance declarations."""

import pytest

from clarinet.config.grid_conformance import validate_grid_conformance
from clarinet.exceptions.domain import RecordConstraintViolationError
from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinitionRead, FileRole


def _rt(*files: FileDefinitionRead, level: str = "SERIES"):
    """Minimal duck-typed RecordTypeCreate stand-in for the validator."""

    class _RT:
        name = "seg-task"

    rt = _RT()
    rt.level = DicomQueryLevel(level)
    rt.file_registry = list(files)
    return rt


def _fd(name, **kw):
    kw.setdefault("pattern", f"{name}.nrrd")
    kw.setdefault("role", FileRole.OUTPUT)
    return FileDefinitionRead(name=name, **kw)


def test_valid_declaration_passes():
    volume = _fd("volume", pattern="volume.nii.gz", role=FileRole.INPUT)
    seg = _fd("seg", grid_conform_to="volume")
    validate_grid_conformance(_rt(volume, seg))  # no raise


def test_no_declaration_passes():
    validate_grid_conformance(_rt(_fd("seg")))  # no raise


def test_unknown_reference_rejected():
    seg = _fd("seg", grid_conform_to="nope")
    with pytest.raises(RecordConstraintViolationError, match="unknown"):
        validate_grid_conformance(_rt(seg))


def test_reference_not_bound_to_record_type_rejected():
    # 'volume' exists as a definition but this record type does not bind it.
    volume = _fd("volume", pattern="volume.nii.gz", role=FileRole.INPUT)
    seg = _fd("seg", grid_conform_to=volume.name)
    with pytest.raises(RecordConstraintViolationError, match=r"unknown.*Bound files: \['seg'\]"):
        validate_grid_conformance(_rt(seg))  # volume deliberately NOT bound


def test_self_reference_rejected():
    seg = _fd("seg", grid_conform_to="seg")
    with pytest.raises(
        RecordConstraintViolationError, match=r"RecordType 'seg-task' file 'seg'.*itself"
    ):
        validate_grid_conformance(_rt(seg))


def test_finer_level_reference_rejected():
    volume = _fd(
        "volume",
        pattern="volume.nii.gz",
        role=FileRole.INPUT,
        level=DicomQueryLevel.SERIES,
    )
    seg = _fd("seg", grid_conform_to="volume", level=DicomQueryLevel.STUDY)
    with pytest.raises(RecordConstraintViolationError, match="finer"):
        validate_grid_conformance(_rt(volume, seg, level="SERIES"))


def test_finer_level_reference_rejected_via_level_inheritance():
    # seg.level is None here, so it must inherit rt.level (STUDY) rather than
    # some hardcoded constant -- only then is STUDY < SERIES actually "finer".
    volume = _fd(
        "volume",
        pattern="volume.nii.gz",
        role=FileRole.INPUT,
        level=DicomQueryLevel.SERIES,
    )
    seg = _fd("seg", grid_conform_to="volume")
    with pytest.raises(RecordConstraintViolationError, match="finer"):
        validate_grid_conformance(_rt(volume, seg, level="STUDY"))


def test_collection_subject_rejected():
    volume = _fd("volume", pattern="volume.nii.gz", role=FileRole.INPUT)
    seg = _fd("seg", grid_conform_to="volume", multiple=True)
    with pytest.raises(RecordConstraintViolationError, match="collection"):
        validate_grid_conformance(_rt(volume, seg))


def test_collection_reference_rejected():
    volume = _fd("volume", pattern="volume.nii.gz", role=FileRole.INPUT, multiple=True)
    seg = _fd("seg", grid_conform_to="volume")
    with pytest.raises(RecordConstraintViolationError, match="collection"):
        validate_grid_conformance(_rt(volume, seg))


def test_non_image_extension_rejected():
    meta = _fd("meta", pattern="meta.json", role=FileRole.INPUT)
    seg = _fd("seg", grid_conform_to="meta")
    with pytest.raises(RecordConstraintViolationError, match="not a readable image"):
        validate_grid_conformance(_rt(meta, seg))
