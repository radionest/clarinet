"""Grid-conformance declaration on config primitives."""

import pytest
from pydantic import ValidationError

from clarinet.config.primitives import FileDef, FileRef, fileref_to_file_definition


def test_object_reference_resolves_after_name_assignment():
    volume = FileDef(pattern="volume.nii.gz", level="SERIES")
    seg = FileDef(pattern="seg.nrrd", level="SERIES", grid_conform_to=volume)
    # Names are assigned by the loader *after* import, mirroring
    # _set_file_names_from_module.
    assert seg.grid_conform_to is volume
    volume.name = "volume"
    seg.name = "seg"

    dto = fileref_to_file_definition(FileRef(seg, "output"))
    assert dto.grid_conform_to == "volume"


def test_string_reference_passes_through():
    seg = FileDef(pattern="seg.nrrd", level="SERIES", grid_conform_to="volume")
    seg.name = "seg"
    dto = fileref_to_file_definition(FileRef(seg, "output"))
    assert dto.grid_conform_to == "volume"


def test_unnamed_object_reference_raises():
    volume = FileDef(pattern="volume.nii.gz", level="SERIES")  # never named
    seg = FileDef(pattern="seg.nrrd", level="SERIES", grid_conform_to=volume)
    seg.name = "seg"
    with pytest.raises(ValueError, match="not a module-level variable"):
        fileref_to_file_definition(FileRef(seg, "output"))


def test_action_passes_through():
    seg = FileDef(
        pattern="seg.nrrd",
        level="SERIES",
        grid_conform_to="volume",
        on_grid_mismatch="conform",
    )
    seg.name = "seg"
    dto = fileref_to_file_definition(FileRef(seg, "output"))
    assert dto.on_grid_mismatch == "conform"


def test_unknown_action_is_rejected():
    with pytest.raises(ValidationError):
        FileDef(pattern="seg.nrrd", level="SERIES", on_grid_mismatch="explode")


def test_absent_declaration_stays_none():
    seg = FileDef(pattern="seg.nrrd", level="SERIES")
    seg.name = "seg"
    dto = fileref_to_file_definition(FileRef(seg, "output"))
    assert dto.grid_conform_to is None
    assert dto.on_grid_mismatch is None


def test_toml_export_includes_declaration():
    from clarinet.config.toml_exporter import _record_type_to_toml_dict
    from clarinet.models.file_schema import FileDefinitionRead, FileRole

    class _RT:
        name = "seg-task"

    rt = _RT()
    rt.file_registry = [
        FileDefinitionRead(
            name="seg",
            pattern="seg.nrrd",
            role=FileRole.OUTPUT,
            grid_conform_to="volume",
            on_grid_mismatch="conform",
        )
    ]
    entry = _record_type_to_toml_dict(rt)["file_registry"][0]
    assert entry["grid_conform_to"] == "volume"
    assert entry["on_grid_mismatch"] == "conform"


def test_toml_export_omits_absent_declaration():
    from clarinet.config.toml_exporter import _record_type_to_toml_dict
    from clarinet.models.file_schema import FileDefinitionRead, FileRole

    class _RT:
        name = "seg-task"

    rt = _RT()
    rt.file_registry = [FileDefinitionRead(name="seg", pattern="seg.nrrd", role=FileRole.OUTPUT)]
    entry = _record_type_to_toml_dict(rt)["file_registry"][0]
    assert "grid_conform_to" not in entry
    assert "on_grid_mismatch" not in entry
