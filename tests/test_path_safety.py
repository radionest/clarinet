"""Tests for the path-safety primitives in ``clarinet.files._template``.

Pure unit tests, stdlib-only inputs, mirroring the style of
``tests/test_path_template_renderer.py``. These cover the value guard, the
lexical containment check, and the config-time pattern validator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clarinet.exceptions.domain import AnonPathError, ConfigurationError, UnsafePathError
from clarinet.files._template import assert_path_safe_value, join_within


class TestUnsafePathErrorTaxonomy:
    def test_subclasses_configuration_error(self):
        assert issubclass(UnsafePathError, ConfigurationError)

    def test_is_not_an_anon_path_error(self):
        # Four sites catch AnonPathError and degrade: Files.for_reader retries
        # with a raw-UID fallback, dicomweb/cache.py and tasks/cache_dicomweb.py
        # log and skip, cli/anon.py counts a failure. A traversal must never
        # degrade into any of those.
        assert not issubclass(UnsafePathError, AnonPathError)


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
