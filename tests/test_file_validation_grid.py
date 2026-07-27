"""Grid-conformance checking inside FileValidator.validate."""

from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import numpy as np

from clarinet.models.file_schema import FileDefinitionRead, FileRole
from clarinet.services.file_validation import FileValidator
from clarinet.services.image.image import FileType, Image

_Z_FLIP = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]])


def _write(path: Path, *, direction=None, shape=(6, 6, 6), origin=(0.0, 0.0, 0.0)):
    img = Image()
    img.spacing = (1.0, 1.0, 1.0)
    img.origin = origin
    img.direction = np.eye(3) if direction is None else direction
    img.img = np.zeros(shape, dtype=np.uint8)
    return img.save_as(path, FileType.NIFTI)


class _Rec:
    """Duck-typed record for Files.render_for — patterns here are literal."""

    id = 1
    patient_id = "p1"
    study_uid = "s1"
    series_uid = "se1"
    user_id = None
    data: ClassVar[dict] = {}
    parent_record_id = None
    # fields_from() unconditionally reads record.record_type.name even for a
    # literal pattern with no {record_type...} placeholder — it builds the
    # whole placeholder dict eagerly, not lazily per referenced token.
    record_type = SimpleNamespace(name="rec")


def _defs(**seg_kw):
    volume = FileDefinitionRead(
        name="volume", pattern="volume.nii", role=FileRole.INPUT, required=True
    )
    seg = FileDefinitionRead(
        name="seg", pattern="seg.nii", role=FileRole.INPUT, required=True, **seg_kw
    )
    return [volume, seg]


def test_same_grid_passes(tmp_path):
    _write(tmp_path / "volume.nii")
    _write(tmp_path / "seg.nii")
    result = FileValidator(_defs(grid_conform_to="volume")).validate(_Rec(), tmp_path)
    assert result.valid, result.errors


def test_mirrored_grid_is_a_mismatch(tmp_path):
    _write(tmp_path / "volume.nii")
    _write(tmp_path / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    result = FileValidator(_defs(grid_conform_to="volume")).validate(_Rec(), tmp_path)
    assert not result.valid
    assert [e.error_type for e in result.errors] == ["grid_mismatch"]


def test_foreign_grid_is_a_mismatch(tmp_path):
    _write(tmp_path / "volume.nii")
    _write(tmp_path / "seg.nii", shape=(8, 8, 8))
    result = FileValidator(_defs(grid_conform_to="volume")).validate(_Rec(), tmp_path)
    assert not result.valid
    assert [e.error_type for e in result.errors] == ["grid_mismatch"]


def test_missing_reference_is_a_mismatch(tmp_path):
    _write(tmp_path / "seg.nii")
    result = FileValidator(_defs(grid_conform_to="volume")).validate(_Rec(), tmp_path)
    assert not result.valid
    assert "grid_mismatch" in [e.error_type for e in result.errors]


def test_absent_subject_is_skipped_not_mismatched(tmp_path):
    _write(tmp_path / "volume.nii")
    defs = _defs(grid_conform_to="volume")
    defs[1].required = False  # absent + optional -> no error at all
    result = FileValidator(defs).validate(_Rec(), tmp_path)
    assert result.valid, result.errors


def test_no_declaration_reads_no_grid(tmp_path, monkeypatch):
    _write(tmp_path / "volume.nii")
    _write(tmp_path / "seg.nii")

    def _boom(*a, **kw):
        raise AssertionError("read_grid must not be called without a declaration")

    monkeypatch.setattr("clarinet.services.file_validation.read_grid", _boom)
    result = FileValidator(_defs()).validate(_Rec(), tmp_path)
    assert result.valid, result.errors


def test_input_reference_bound_as_output_resolves(tmp_path):
    """grid_conform_to resolution spans the full registry, not just the validated set.

    validate_record_files validates only the INPUT-role subset, but a
    grid_conform_to reference may be bound as OUTPUT (or INTERMEDIATE) on the
    same record type — Task 3's config-load validator accepts any role
    pairing. Passing the full registry separately (the controller-ruling
    __init__ shape) must resolve the reference instead of reporting it "not
    bound to this record type".
    """
    _write(tmp_path / "volume.nii")
    _write(tmp_path / "seg.nii")
    seg = FileDefinitionRead(
        name="seg",
        pattern="seg.nii",
        role=FileRole.INPUT,
        required=True,
        grid_conform_to="volume",
    )
    volume_as_output = FileDefinitionRead(
        name="volume", pattern="volume.nii", role=FileRole.OUTPUT, required=True
    )
    result = FileValidator([seg], registry=[seg, volume_as_output]).validate(_Rec(), tmp_path)
    assert result.valid, result.errors
