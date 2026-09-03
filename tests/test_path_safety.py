"""Tests for the path-safety primitives in ``clarinet.files._template``.

Pure unit tests, stdlib-only inputs, mirroring the style of
``tests/test_path_template_renderer.py``. These cover the value guard, the
lexical containment check, and the config-time pattern validator.

The final class steps up one layer, to ``clarinet.models.file_schema``, to pin
where ``validate_file_pattern`` is actually wired in (``FileDefinitionRead``)
and where it deliberately is not (``FileDefinition``, a ``table=True`` model
SQLModel skips Pydantic validation on).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clarinet.exceptions.domain import AnonPathError, ConfigurationError, UnsafePathError
from clarinet.files._patterns import fields_from
from clarinet.files._template import (
    KNOWN_PLACEHOLDERS,
    OPTIONAL_PLACEHOLDERS,
    RenderMode,
    assert_path_safe_value,
    join_within,
    render_template,
    validate_file_pattern,
)


class TestUnsafePathErrorTaxonomy:
    def test_subclasses_configuration_error(self):
        assert issubclass(UnsafePathError, ConfigurationError)

    def test_is_not_an_anon_path_error(self):
        # Four sites catch AnonPathError and degrade: Files.for_reader retries
        # with a raw-UID fallback, dicomweb/cache.py and tasks/cache_dicomweb.py
        # log and skip, cli/anon.py counts a failure. A traversal must never
        # degrade into any of those.
        assert not issubclass(UnsafePathError, AnonPathError)

    def test_is_re_exported_from_the_exceptions_package(self):
        # Every other domain exception is reachable as
        # `from clarinet.exceptions import X`, AnonPathError included. Project
        # plan/ code and pipeline tasks that want `except UnsafePathError`
        # should not have to reach into the private `.domain` leaf.
        import clarinet.exceptions as exceptions

        assert exceptions.UnsafePathError is UnsafePathError
        assert "UnsafePathError" in exceptions.__all__

    def test_is_not_a_value_error(self):
        # render_template's _replace swallows ValueError in LENIENT mode. If a
        # future refactor moved the path_safe guard inside that try/except, this
        # is the second line of defence — and the NOTE comment above the guard
        # call in _replace claims it exists.
        assert not issubclass(UnsafePathError, ValueError)


class TestAssertPathSafeValue:
    @pytest.mark.parametrize(
        "value",
        [
            "/etc/passwd",  # absolute: pathlib would discard the base entirely
            "../../etc/passwd",  # classic relative traversal
            "..",  # the split form, where the pattern supplies the "/"
            ".",  # current-directory reference
            "a/b",  # a coerced collection can produce this
            "back\\slash",  # POSIX ignores it; the analyst's Slicer may not
            "nul\x00byte",
        ],
    )
    def test_rejects(self, value):
        with pytest.raises(UnsafePathError):
            assert_path_safe_value("patient_id", value)

    @pytest.mark.parametrize(
        "value",
        ["CT_SR", "mask.seg.nrrd", "1.2.840.113619.2.55", "a.b.c", "-dash", "_under"],
    )
    def test_accepts(self, value):
        assert assert_path_safe_value("patient_id", value) is None

    def test_error_names_the_key(self):
        with pytest.raises(UnsafePathError) as exc:
            assert_path_safe_value("data.secret_mrn", "/etc/passwd")
        assert "data.secret_mrn" in str(exc.value)

    def test_message_omits_the_value_but_the_exception_carries_it(self):
        with pytest.raises(UnsafePathError) as exc:
            assert_path_safe_value("data.secret_mrn", "/etc/passwd")
        assert "/etc/passwd" not in str(exc.value)
        assert exc.value.value == "/etc/passwd"


BASE = Path("/data/storage/anon_1/study/series")


class TestJoinWithin:
    @pytest.mark.parametrize(
        "rendered",
        [
            "/etc/passwd",  # absolute absorbs the base
            "../sibling.nrrd",
            "../../etc/passwd",
            "sub/../../escape.nrrd",  # normalises out of the base
            "",  # LENIENT rendered a whole-pattern placeholder away
            ".",  # equals the base after normalisation
        ],
    )
    def test_rejects(self, rendered):
        with pytest.raises(UnsafePathError):
            join_within(BASE, rendered)

    @pytest.mark.parametrize(
        "rendered",
        ["mask.nrrd", "mask.seg.nrrd", "seg_7.seg.nrrd", "1.2.840.113619/mask.nrrd"],
    )
    def test_accepts(self, rendered):
        result = join_within(BASE, rendered)
        assert result.is_relative_to(BASE)

    @pytest.mark.parametrize("rendered", [".ssh", ".bashrc", "sub/.hidden", ".txt"])
    def test_accepts_a_dot_leading_basename(self, rendered):
        # A dot-leading basename is a hidden file, not an escape, and it is
        # exactly what an absent optional placeholder renders to
        # ("{parent_id}.txt" for a parentless record). Rejecting it here made
        # every render-then-join site hard-fail where it used to answer "file
        # not found". The pattern that could produce it is rejected at config
        # load instead -- see TestRejectsVanishingPlaceholderShapes below.
        assert join_within(BASE, rendered).is_relative_to(BASE)

    def test_performs_no_filesystem_access(self, monkeypatch):
        # Files.resolve is sync and looped over the whole registry by
        # build_slicer_context; a syscall here would be a latency regression.
        def explode(*args, **kwargs):
            raise AssertionError("join_within must not touch the filesystem")

        monkeypatch.setattr(Path, "resolve", explode)
        monkeypatch.setattr(Path, "exists", explode)
        assert join_within(BASE, "mask.nrrd") == BASE / "mask.nrrd"

    def test_message_omits_the_rendered_name_but_the_exception_carries_it(self):
        with pytest.raises(UnsafePathError) as exc:
            join_within(BASE, "../MRN_12345.nrrd")
        assert "MRN_12345" not in str(exc.value)
        assert exc.value.value == "../MRN_12345.nrrd"

    def test_rejects_a_nul_byte(self):
        # The persisted-filename site (services/pipeline/context.py) joins a
        # stored RecordFileLink.filename with no value guard upstream, so a NUL
        # in that column passes containment here and then surfaces as an
        # untyped `ValueError: embedded null byte` from inside .is_file() --
        # which the LENIENT renderer's `except ValueError` is built to swallow.
        # Keep the failure typed and at the boundary.
        with pytest.raises(UnsafePathError):
            join_within(BASE, "mask\x00.nrrd")

    def test_dotdot_component_message_omits_the_rendered_name_but_the_exception_carries_it(self):
        # The one remaining raise site whose message interpolates neither
        # `base` nor `rendered`. Reached only for a relative base, which no
        # production call site produces -- kept as defence in depth.
        with pytest.raises(UnsafePathError) as exc:
            join_within(Path(".."), "../MRN_12345.nrrd")
        assert "MRN_12345" not in str(exc.value)
        assert exc.value.value == "../MRN_12345.nrrd"


class TestRenderTemplatePathSafe:
    def test_off_by_default(self):
        out = render_template("{patient_id}", {"patient_id": "/etc/passwd"})
        assert out == "/etc/passwd"

    def test_rejects_when_enabled(self):
        with pytest.raises(UnsafePathError):
            render_template("{patient_id}", {"patient_id": "/etc/passwd"}, path_safe=True)

    def test_lenient_mode_does_not_swallow_the_violation(self):
        # _replace swallows ValueError in LENIENT mode and substitutes "".
        # UnsafePathError must escape that handler, or every violation would be
        # silently rewritten to an empty string.
        with pytest.raises(UnsafePathError):
            render_template(
                "seg_{patient_id}.nrrd",
                {"patient_id": "../../etc/passwd"},
                mode=RenderMode.LENIENT,
                path_safe=True,
            )

    def test_runs_after_coercion(self):
        # A list coerces to "a_b" by default; with "/" as the separator it
        # becomes "a/b", which only a post-coercion check can catch.
        with pytest.raises(UnsafePathError):
            render_template("{mods}", {"mods": ["a", "b"]}, list_separator="/", path_safe=True)

    def test_missing_substitution_is_not_a_violation(self):
        assert render_template("f_{nope}.txt", {"x": 1}, path_safe=True) == "f_.txt"

    def test_legitimate_values_pass(self):
        out = render_template(
            "seg_{id}_{mods}.seg.nrrd", {"id": 7, "mods": ["CT", "SR"]}, path_safe=True
        )
        assert out == "seg_7_CT_SR.seg.nrrd"


# Un-banning under #552 moves entries from this list to LEGAL_PATTERNS below.
BANNED_DATA_PATTERNS = [
    "birads_{data.BIRADS_R}.txt",
    "report_{data.timepoint}.pdf",
    "{data.side}_mask.nrrd",
    "{data}",
    "seg_{id}_{data.lesion}.nrrd",
    "{{data.side}}_mask.nrrd",  # escaped braces: Formatter hides it, the renderer sees it
]

UNSAFE_LITERAL_PATTERNS = [
    "/abs/mask.nrrd",
    "../x.nrrd",
    "sub/../x.nrrd",
    "back\\slash.nrrd",
    "outputs/",
    "",
    "   ",
    ".hidden.nrrd",
    "nul\x00.nrrd",
]

# Patterns whose LITERAL text is safe, but which render to a name the
# working-directory join cannot take once an optional placeholder is absent.
# Each is a config a deployment could legally hold before this rule existed;
# each now fails at config load instead of on somebody's request.
VANISHING_PLACEHOLDER_PATTERNS = [
    "{parent_id}.txt",  # parentless record -> ".txt"
    "{user_id}",  # unassigned record -> ""
    "{study_uid}.json",  # patient-level record -> ".json"
    "{series_uid}.dcm",  # study-level record -> ".dcm"
    "{study_uid}/mask.nrrd",  # patient-level record -> "/mask.nrrd"
    "sub/{parent_id}.nrrd",  # parentless record -> "sub/.nrrd"
    "{user_id}/{parent_id}/x.nrrd",  # both absent -> "//x.nrrd"
    "{parent_id}{user_id}.nrrd",  # both absent -> ".nrrd"
    "{parent_id}.",  # parentless record -> "." -- the working dir itself
    "{user_id}.",  # unassigned record -> "."
    "{parent_id}.{user_id}",  # both absent -> "."
    "sub/{parent_id}.",  # parentless record -> "sub/." -- the "sub" dir itself
]

LEGAL_PATTERNS = [
    "mask.nrrd",
    "mask.seg.nrrd",
    "seg_{id}.seg.nrrd",
    "segmentation_{user_id}.seg.nrrd",
    "study_{study_uid}/mask.nrrd",
    "master_model.seg.nrrd",
    "report_{parent_id}.pdf",
    "{id}.nrrd",  # {id} is never absent, so it needs no literal prefix
]


class TestValidateFilePattern:
    @pytest.mark.parametrize("pattern", BANNED_DATA_PATTERNS)
    def test_rejects_data_placeholders(self, pattern):
        with pytest.raises(ValueError, match="data"):
            validate_file_pattern(pattern)

    def test_ban_error_suggests_replacements(self):
        with pytest.raises(ValueError) as exc:
            validate_file_pattern("birads_{data.BIRADS_R}.txt")
        message = str(exc.value)
        assert "data.BIRADS_R" in message
        assert "{id}" in message

    @pytest.mark.parametrize("pattern", UNSAFE_LITERAL_PATTERNS)
    def test_rejects_unsafe_literal_text(self, pattern):
        with pytest.raises(ValueError):
            validate_file_pattern(pattern)

    @pytest.mark.parametrize("pattern", LEGAL_PATTERNS)
    def test_accepts(self, pattern):
        assert validate_file_pattern(pattern) == pattern

    def test_placeholder_content_is_masked_before_literal_check(self):
        # A placeholder NAME containing a dot must not trip the dot-leading
        # basename rule, and a placeholder standing alone as the basename is fine.
        assert validate_file_pattern("{record_type.name}.nrrd") == "{record_type.name}.nrrd"


class TestRejectsVanishingPlaceholderShapes:
    """Pins the rule that replaced ``join_within``'s empty / dot-leading checks.

    Those two rules used to fire at request time, on the rendered name. That
    made a legitimately absent placeholder -- a parentless record's
    ``{parent_id}``, a patient-level record's ``{study_uid}``, an unassigned
    record's ``{user_id}`` -- a hard failure on every render-then-join path,
    where the same config previously answered "file not found". The rule now
    runs once, at config load, against the pattern's worst-case render.
    """

    @pytest.mark.parametrize("pattern", VANISHING_PLACEHOLDER_PATTERNS)
    def test_rejects(self, pattern):
        with pytest.raises(ValueError, match="would render to"):
            validate_file_pattern(pattern)

    @pytest.mark.parametrize("pattern", VANISHING_PLACEHOLDER_PATTERNS)
    def test_collection_patterns_are_exempt(self, pattern):
        """A collection globs, it never renders.

        ``glob_file_paths`` substitutes every placeholder with ``*``, so
        ``{parent_id}.nrrd`` becomes ``*.nrrd`` — a legitimate collection
        pattern, not a degenerate name. Applying the rule to collections
        aborted startup for configs that work fine.
        """
        assert validate_file_pattern(pattern, is_collection=True) == pattern

    def test_collection_exemption_does_not_reach_the_literal_rules(self):
        """Only the render-time rule is skipped; literal text is still judged."""
        with pytest.raises(ValueError, match="must be relative"):
            validate_file_pattern("/abs/{parent_id}.nrrd", is_collection=True)
        with pytest.raises(ValueError, match="may not interpolate record data"):
            validate_file_pattern("{data.side}_x.nrrd", is_collection=True)

    def test_error_shows_the_degenerate_render_and_names_the_fix(self):
        with pytest.raises(ValueError) as exc:
            validate_file_pattern("{parent_id}.txt")
        message = str(exc.value)
        assert "'.txt'" in message  # what it renders to, not just the pattern
        assert "dot-leading basename" in message
        assert "report_{parent_id}.pdf" in message  # the shape that works

    @pytest.mark.parametrize(
        "pattern",
        ["{id}.nrrd", "{record_type.name}.nrrd", "{patient_id}.nrrd", "{origin_type}/x.nrrd"],
    )
    def test_accepts_a_placeholder_that_is_never_absent(self, pattern):
        # Only OPTIONAL_PLACEHOLDERS are erased for the worst-case render;
        # every other placeholder is masked to a non-empty sentinel, so a
        # pattern resting on one of those needs no literal prefix.
        assert validate_file_pattern(pattern) == pattern

    def test_rule_runs_after_the_literal_text_rules(self):
        # "../{user_id}" trips both families. The literal '..' message is the
        # more specific one and must win.
        with pytest.raises(ValueError, match=r"'\.\.' component"):
            validate_file_pattern("../{user_id}")

    def test_rejects_a_render_that_collapses_to_a_bare_directory_reference(self):
        # PurePosixPath('.').name is '' while PurePosixPath('..').name is '..',
        # so the dot-leading basename rule never fires for a worst-case render
        # of "." -- it needs its own segment-level check. Unguarded, the pattern
        # loaded fine and every resolve() 500ed at join_within's equals-the-base
        # branch instead, including the build_slicer_context loop.
        with pytest.raises(ValueError, match="bare directory reference"):
            validate_file_pattern("{parent_id}.")


class TestRejectsUnknownPlaceholders:
    """A placeholder the renderer cannot resolve must fail at config load.

    ``render_template`` runs LENIENT, so an unrecognised name substitutes ``""``
    rather than raising: a typo'd ``{studyuid}`` silently became ``""`` and the
    failure surfaced at ``join_within`` on somebody's request -- or, worse,
    never surfaced at all when the pattern still rendered to a usable name.
    """

    @pytest.mark.parametrize(
        "pattern",
        [
            "{studyuid}",  # typo for {study_uid} -> "" -> equals the base
            "{studyuid}/mask.nrrd",  # -> "/mask.nrrd" -> absolute, absorbs the base
            "{studyuid}.nrrd",  # -> ".nrrd" -> a hidden file, silently wrong
            "{Study_UID}.nrrd",  # the catalogue is case-sensitive
            "{record_type}.nrrd",  # a Mapping coerces to "" under LENIENT
        ],
    )
    def test_rejects(self, pattern):
        with pytest.raises(ValueError, match="unknown placeholder"):
            validate_file_pattern(pattern)

    def test_error_names_the_offender_and_the_catalogue(self):
        with pytest.raises(ValueError) as exc:
            validate_file_pattern("seg_{studyuid}.nrrd")
        message = str(exc.value)
        assert "studyuid" in message
        assert "study_uid" in message  # the catalogue, so the typo is obvious

    @pytest.mark.parametrize("pattern", ["{studyuid}.nrrd", "slice_{n}.dcm", "{frame}/x.nrrd"])
    def test_collections_are_exempt(self, pattern):
        """In a collection the placeholder's *name* carries no meaning.

        ``glob_file_paths`` substitutes ``*`` for every placeholder whatever it
        is called, so ``slice_{n}.dcm`` -> ``slice_*.dcm`` is a deliberate
        positional-wildcard idiom, and a misspelling is harmless for the same
        reason -- ``{study_uid}`` and ``{studyuid}`` both glob to ``*``.
        """
        assert validate_file_pattern(pattern, is_collection=True) == pattern

    def test_data_ban_wins_over_the_unknown_placeholder_message(self):
        # {data.*} is unknown to the catalogue too, but its own message carries
        # the #552 migration note and must not be shadowed.
        with pytest.raises(ValueError, match="may not interpolate record data"):
            validate_file_pattern("birads_{data.BIRADS_R}.txt")

    @pytest.mark.parametrize("name", sorted(KNOWN_PLACEHOLDERS))
    def test_accepts_every_catalogued_placeholder(self, name):
        pattern = "f_{" + name + "}.nrrd"
        assert validate_file_pattern(pattern) == pattern

    def test_catalogue_is_rebuilt_from_the_renderer_it_guards(self):
        """The catalogue must track ``fields_from``, or it refuses a legal pattern.

        A hand-maintained list of names silently goes stale the moment
        ``fields_from`` grows a key: the new placeholder would work perfectly at
        render time and be rejected at startup. Rebuild the set from the
        renderer's own output instead of trusting the transcription.
        """
        record = MagicMock()
        record.record_type.name = "ct-segmentation"
        # Deliberately empty: `{data.*}` is banned (#552) and its keys are
        # per-record anyway, so `data` contributes nothing to a static catalogue.
        record.data = {}

        resolvable: set[str] = set()
        for key, value in fields_from(record).items():
            if isinstance(value, Mapping):
                resolvable |= {f"{key}.{leaf}" for leaf in value}
            else:
                resolvable.add(key)

        assert resolvable == KNOWN_PLACEHOLDERS

    def test_optional_placeholders_are_a_subset_of_the_catalogue(self):
        # An optional placeholder outside the catalogue would be erased for the
        # worst-case render and then rejected as unknown — an unreachable rule.
        assert OPTIONAL_PLACEHOLDERS <= KNOWN_PLACEHOLDERS


class TestFileDefinitionReadPatternValidation:
    """Pins where ``validate_file_pattern`` is wired into the model layer."""

    def test_file_definition_read_rejects_banned_pattern(self):
        from pydantic import ValidationError

        from clarinet.models.file_schema import FileDefinitionRead

        with pytest.raises(ValidationError):
            FileDefinitionRead(name="birads_file", pattern="birads_{data.BIRADS_R}.txt")

    def test_table_model_is_intentionally_unvalidated(self):
        # Pins the reason there is no validator on FileDefinition: SQLModel skips
        # validation on table=True models, so one there would be dead code. If this
        # ever starts raising, add the validator and delete this test.
        from clarinet.models.file_schema import FileDefinition

        assert FileDefinition(name="1bad", pattern="birads_{data.X}.txt").name == "1bad"
