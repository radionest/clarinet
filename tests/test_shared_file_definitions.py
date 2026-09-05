"""A FileDefinition row is shared by every RecordType binding it.

Pure-unit coverage of ``validate_shared_file_definitions`` (the config-load
cross-type check) and of the ``FILE_DEFINITION_FIELDS`` tuple it iterates.
The DB-backed half — reconcile aborting before any write — lives in
``tests/integration/test_config_reconciler.py``.
"""

import pytest

from clarinet.config.reconciler import validate_shared_file_definitions
from clarinet.exceptions.domain import RecordConstraintViolationError
from clarinet.models.file_schema import FILE_DEFINITION_FIELDS, FileDefinition
from clarinet.models.record import RecordTypeCreate

_VOLUME: dict[str, object] = {
    "name": "volume",
    "pattern": "volume.nii.gz",
    "role": "input",
    "required": True,
}
_PLAIN_SEG: dict[str, object] = {
    "name": "seg",
    "pattern": "seg_{id}.nrrd",  # {id} discriminates -> path-uniqueness no-ops
    "role": "output",
    "required": True,
    "description": "lesion mask",
    "level": "SERIES",
}
_GUARDED_SEG: dict[str, object] = {
    **_PLAIN_SEG,
    "grid_conform_to": "volume",
    "on_grid_mismatch": "reject",
}


def _binders(seg_in_a: dict[str, object], seg_in_b: dict[str, object]) -> list[RecordTypeCreate]:
    """Two RecordTypes binding ``volume`` and ``seg``, each with its own seg entry."""
    return [
        RecordTypeCreate(name="type-a", level="SERIES", file_registry=[_VOLUME, seg_in_a]),
        RecordTypeCreate(name="type-b", level="SERIES", file_registry=[_VOLUME, seg_in_b]),
    ]


def test_shared_file_declared_identically_passes() -> None:
    config = [*_binders(_GUARDED_SEG, dict(_GUARDED_SEG)), RecordTypeCreate(name="type-c")]
    validate_shared_file_definitions(config)  # no raise; type-c binds nothing


@pytest.mark.parametrize(
    "override",
    [{"role": "input"}, {"required": False}, {"allow_path_collision": True}],
    ids=["role", "required", "allow_path_collision"],
)
def test_binding_fields_may_differ_between_types(override: dict[str, object]) -> None:
    """role/required/allow_path_collision live on the link row, not the shared file row."""
    validate_shared_file_definitions(_binders(_PLAIN_SEG, {**_PLAIN_SEG, **override}))


@pytest.mark.parametrize(
    ("field", "other"),
    [
        ("pattern", "seg_{id}.nii.gz"),
        ("description", "a different purpose"),
        ("multiple", True),
        ("level", "STUDY"),
    ],
)
def test_row_field_disagreement_names_file_field_and_both_types(field: str, other: object) -> None:
    with pytest.raises(
        RecordConstraintViolationError, match=rf"'seg'.*'type-a'.*'type-b'.*{field}"
    ):
        validate_shared_file_definitions(_binders(_PLAIN_SEG, {**_PLAIN_SEG, field: other}))


def test_omitting_grid_declaration_in_one_type_is_rejected() -> None:
    """The #499 hole in config form: type-b binds seg without the guard type-a
    declares. Last-write-wins would leave the shared row in whichever state
    reconciled last, so this must be a config error, not a silent flip.
    """
    with pytest.raises(
        RecordConstraintViolationError, match=r"'seg'.*'type-a'.*'type-b'.*grid_conform_to"
    ):
        validate_shared_file_definitions(_binders(_GUARDED_SEG, _PLAIN_SEG))


def test_grid_mismatch_action_disagreement_is_rejected() -> None:
    with pytest.raises(
        RecordConstraintViolationError, match=r"'seg'.*'type-a'.*'type-b'.*on_grid_mismatch"
    ):
        validate_shared_file_definitions(
            _binders(_GUARDED_SEG, {**_GUARDED_SEG, "on_grid_mismatch": "conform"})
        )


def test_file_definition_fields_cover_every_row_column() -> None:
    """Drift guard: a column added to FileDefinition must join the tuple, or the
    upsert, the cross-type check and the API merge silently ignore it.
    """
    assert set(FILE_DEFINITION_FIELDS) == set(FileDefinition.model_fields) - {"id", "name"}
