"""Tests for clarinet.config.primitives: RecordDef.unique_by, FileRef.allow_path_collision."""

import inspect
import typing

import pytest
from pydantic import BaseModel, ValidationError

from clarinet.config import primitives
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


def test_filedef_requires_pattern_when_omitted() -> None:
    # pattern is an explicit, _UNSET-defaulted __init__ param (mirrors level)
    # and has no pydantic default either -- omitting it while still passing a
    # widened param like level must raise pydantic's own "field required"
    # error, not silently construct a half-initialized FileDef.
    with pytest.raises(ValidationError):
        FileDef(level="SERIES")


def test_recorddef_requires_name_when_omitted() -> None:
    # Same shape as test_filedef_requires_pattern_when_omitted above: name has
    # no explicit __init__ param and no pydantic default, so omitting it must
    # raise pydantic's own "field required" error rather than silently
    # constructing a nameless RecordDef.
    with pytest.raises(ValidationError):
        RecordDef()


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


def test_recorddef_accepts_uppercase_string_level() -> None:
    r = RecordDef(name="first-check", level="STUDY")
    assert r.level is DicomQueryLevel.STUDY


def test_recorddef_accepts_lowercase_string_level() -> None:
    # _coerce_dicom_level upper()s its input, so this is valid at runtime — the
    # plan cites this as the reason level's union is `| str`, not Literal[...].
    r = RecordDef(name="first-check", level="study")
    assert r.level is DicomQueryLevel.STUDY


def test_recorddef_unique_per_user_still_warns_and_maps() -> None:
    with pytest.warns(DeprecationWarning):
        r = RecordDef(name="first-check", unique_per_user=True)
    assert r.unique_by == legacy_unique_per_user(True)


def test_recorddef_explicit_unique_by_beats_unique_per_user() -> None:
    with pytest.warns(DeprecationWarning):
        r = RecordDef(name="first-check", unique_per_user=True, unique_by=["parent"])
    assert r.unique_by == frozenset({"parent"})


def test_recorddef_explicit_none_unique_by_beats_unique_per_user() -> None:
    # unique_by=None is an explicit, meaningful value ("no uniqueness") distinct
    # from omission — it must beat the deprecated flag exactly like any other
    # explicit unique_by does above, not get silently overridden by it.
    with pytest.warns(DeprecationWarning):
        r = RecordDef(name="first-check", unique_by=None, unique_per_user=True)
    assert r.unique_by is None


# --- Guard: every mode="before" coercion validator has a matching, correctly-typed,
# correctly-required __init__ param. Discovery-based so a new coerced field is
# covered on arrival rather than needing a fourth hand-written test. ---


def _coerced_fields(model: type[BaseModel]) -> set[str]:
    """Field names carrying a ``mode="before"`` field validator on *model*."""
    found: set[str] = set()
    for decorator in model.__pydantic_decorators__.field_validators.values():
        if decorator.info.mode == "before":
            found.update(decorator.info.fields)
    return found


def _models() -> list[type[BaseModel]]:
    """Model classes defined in primitives.py, deduped.

    ``File = FileDef`` and ``RecordTypeDef = RecordDef`` are backward-compat
    aliases for the same class object, not separate models — iterating
    ``vars(primitives)`` directly would yield each class twice and parametrize
    it twice for nothing.
    """
    seen: set[int] = set()
    models: list[type[BaseModel]] = []
    for obj in vars(primitives).values():
        if not (
            inspect.isclass(obj)
            and issubclass(obj, BaseModel)
            and obj.__module__ == primitives.__name__
        ):
            continue
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        models.append(obj)
    return models


def test_guard_discovery_still_finds_the_known_models() -> None:
    """Floor for the two parametrized guards below.

    ``@pytest.mark.parametrize("model", _models(), ...)`` with an empty list
    collects as a single SKIPPED case (pytest's default
    ``empty_parameter_set_mark``), not a failure — if ``_models()`` or
    ``_coerced_fields()`` silently found nothing (e.g. primitives.py split
    into a package, so the ``obj.__module__ == primitives.__name__`` filter in
    ``_models()`` stops matching), every other test in this file would keep
    passing and both guards would go green for having nothing left to check.
    This is the one assertion that cannot itself go vacuous, so it is what
    actually notices. It also means a newly added coerced field must be added
    to the set below in the same edit — a deliberate, reviewed change, not
    silent coverage growth.
    """
    assert {m.__name__ for m in _models()} == {"FileDef", "FileRef", "RecordDef"}
    assert {(m.__name__, f) for m in _models() for f in _coerced_fields(m)} == {
        ("FileDef", "level"),
        ("FileRef", "role"),
        ("RecordDef", "level"),
        ("RecordDef", "viewer_mode"),
        ("RecordDef", "unique_by"),
    }


@pytest.mark.parametrize("model", _models(), ids=lambda m: m.__name__)
def test_every_coercion_validator_has_a_widened_constructor_param(
    model: type[BaseModel],
) -> None:
    """A coerced field must be an explicit, non-narrow ``__init__`` param.

    ``**kwargs: Any`` deliberately does NOT count — it disables checking rather
    than expressing the accepted type, which is the RecordDef hole this guard
    exists to keep from spreading. Neither do an explicit ``Any``/``object``
    annotation or a missing one: both type-check identically to ``**kwargs: Any``
    (mypy treats a missing annotation as implicit ``Any``), so they must fail
    the same way.
    """
    coerced = _coerced_fields(model)
    if not coerced:
        pytest.skip(f"{model.__name__} has no mode='before' validators")

    signature = inspect.signature(model.__init__)
    hints = typing.get_type_hints(model.__init__)

    for field in sorted(coerced):
        assert field in signature.parameters, (
            f"{model.__name__}.{field} has a mode='before' coercion validator but no "
            f"explicit __init__ param. Widen the constructor (see FileRef.__init__); "
            f"**kwargs: Any does not count."
        )
        declared = hints.get(field)
        field_type = model.model_fields[field].annotation
        if field_type is str:
            # A field whose own annotation is already `str` (e.g. a
            # whitespace-stripping/lowercasing mode="before" validator that
            # doesn't narrow the runtime type at all) has nothing narrower to
            # widen FROM — the checks below would demand an impossible
            # widening on otherwise-correct code. The explicit-param
            # requirement above still applies to it.
            continue
        assert declared != field_type, (
            f"{model.__name__}.__init__ declares {field} as {declared!r}, identical to "
            f"the field annotation. It must admit the looser input its validator "
            f"coerces (e.g. `| str`)."
        )
        # `!=` alone is satisfied by exactly the signatures this test exists to
        # reject: `level: Any` or `level: object` (both equivalent to
        # **kwargs: Any — they disable checking rather than express the
        # accepted type) and a missing annotation (declared is None, and
        # None != field_type). Require an actual, non-trivial union instead.
        # This does NOT catch a union with a wrong extra member, e.g.
        # `DicomQueryLevel | int` — narrower gap, left uncaught rather than
        # chased.
        assert typing.get_args(declared) and declared not in (typing.Any, object), (
            f"{model.__name__}.__init__ declares {field} as {declared!r}, which does "
            f"not express an accepted type. `Any`, `object`, and a missing annotation "
            f"all disable checking exactly like **kwargs: Any does — none of them "
            f"count as widening."
        )
        # Discovery-based equivalent of test_filedef_level_field_annotation_unchanged
        # (above) for every coerced field on every model, not just FileDef.level:
        # the FIELD annotation itself must stay narrow. If it admitted a bare str,
        # the __init__ union would have been promoted onto the field — forbidden,
        # since the field annotation drives pydantic's own validation/serialization.
        assert str not in (field_type, *typing.get_args(field_type)), (
            f"{model.__name__}.model_fields[{field!r}].annotation is {field_type!r}, "
            f"which itself admits a plain str. A mode='before' validator exists to "
            f"coerce str input into a narrower type; if the field itself already "
            f"accepts str, the union was promoted onto the field (forbidden — only "
            f"__init__ may widen, see FileDef.level)."
        )


def _requiredness_mismatches(model: type[BaseModel]) -> list[str]:
    """Fields whose explicit __init__ param disagrees with the field's own requiredness.

    Scope is deliberately narrow: only fields that ARE an explicit, named
    ``__init__`` param are checked. Fields with no explicit param (e.g.
    ``RecordDef.name``, ``RecordDef.description``) fall through
    ``**kwargs: Any`` — that is the accepted blind spot (design decision D8),
    not this guard's job. FileDef has no such fields anymore: ``level`` widens,
    ``pattern`` is ``_UNSET``-defaulted like ``level`` (both required, no
    pydantic default), and ``multiple``/``description``/``name`` mirror their
    own pydantic default directly — see ``FileDef.__init__``. Params that are
    not fields at all (``RecordDef``'s ``role``/``unique_per_user`` aliases)
    are never visited, because this walks ``model_fields``, not the signature.

    A param defaulting to the module's ``_UNSET`` sentinel is exempt in BOTH
    directions. That default means "forward only if the caller actually passed
    something" — it is how a required field (``FileDef.level``) can be widened
    without inventing a fake default of its own, at the cost of mypy's "missing
    argument" check for it (accepted, see design decision D10 — the runtime
    ValidationError remains the real enforcement, exercised by
    ``test_filedef_requires_level_when_omitted`` above). Consequence: if such a
    field's own requiredness later flips (required <-> optional) while its
    param stays ``_UNSET``-defaulted, that is invisible to this guard — the
    signature looks identical either way. What must NOT happen:

      - a required field getting some OTHER, non-sentinel default: that default
        would be forwarded unconditionally, silently supplying a value pydantic
        never declared instead of raising for a missing one (this is the exact
        regression rejected during Task 1 — a hardcoded
        ``= DicomQueryLevel.SERIES`` default for the required ``level`` field);
      - an optional field becoming a genuinely no-default (required) param:
        that forces every caller to pass it, breaking "omitted fields keep
        their pydantic default".
    """
    signature = inspect.signature(model.__init__)
    mismatches: list[str] = []
    for name, field in model.model_fields.items():
        param = signature.parameters.get(name)
        if param is None or param.default is primitives._UNSET:
            continue
        required = field.is_required()
        param_is_required = param.default is inspect.Parameter.empty
        if param_is_required and not required:
            mismatches.append(
                f"{model.__name__}.{name}: __init__ has no default (required) but the "
                f"field is optional in pydantic (default={field.default!r}) — every "
                f"caller is now forced to pass it, which breaks omitted-field defaults."
            )
        elif required and not param_is_required:
            mismatches.append(
                f"{model.__name__}.{name}: __init__ defaults to {param.default!r} but the "
                f"field is required in pydantic with no default of its own — omission "
                f"would silently forward {param.default!r} instead of raising. Use the "
                f"_UNSET sentinel (forward only when passed) instead of a concrete default."
            )
    return mismatches


@pytest.mark.parametrize("model", _models(), ids=lambda m: m.__name__)
def test_widened_constructor_param_requiredness_matches_field(
    model: type[BaseModel],
) -> None:
    """A hand-written ``__init__`` suppresses pydantic's synthesized one, and with
    it mypy's "missing named argument" check (design decision D10). This guard
    is the replacement: for every field exposed as an explicit constructor
    param, that param must not misrepresent whether the field is actually
    required.
    """
    mismatches = _requiredness_mismatches(model)
    assert not mismatches, "\n".join(mismatches)
