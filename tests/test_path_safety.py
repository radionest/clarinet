"""Tests for the path-safety primitives in ``clarinet.files._template``.

Pure unit tests, stdlib-only inputs, mirroring the style of
``tests/test_path_template_renderer.py``. These cover the value guard, the
lexical containment check, and the config-time pattern validator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clarinet.exceptions.domain import AnonPathError, ConfigurationError, UnsafePathError
from clarinet.files._template import (
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
            ".ssh",  # dot-leading basename
            ".bashrc",
            "sub/.hidden",  # dot-leading basename in a subdirectory
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

    def test_dot_leading_basename_message_omits_the_rendered_name_but_the_exception_carries_it(
        self,
    ):
        with pytest.raises(UnsafePathError) as exc:
            join_within(BASE, ".MRN_12345")
        assert "MRN_12345" not in str(exc.value)
        assert exc.value.value == ".MRN_12345"


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

LEGAL_PATTERNS = [
    "mask.nrrd",
    "mask.seg.nrrd",
    "seg_{id}.seg.nrrd",
    "segmentation_{user_id}.seg.nrrd",
    "{study_uid}/mask.nrrd",
    "master_model.seg.nrrd",
    "report_{parent_id}.pdf",
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
