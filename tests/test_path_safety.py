"""Tests for the path-safety primitives in ``clarinet.files._template``.

Pure unit tests, stdlib-only inputs, mirroring the style of
``tests/test_path_template_renderer.py``. These cover the value guard, the
lexical containment check, and the config-time pattern validator.
"""

from __future__ import annotations

from clarinet.exceptions.domain import AnonPathError, ConfigurationError, UnsafePathError


class TestUnsafePathErrorTaxonomy:
    def test_subclasses_configuration_error(self):
        assert issubclass(UnsafePathError, ConfigurationError)

    def test_is_not_an_anon_path_error(self):
        # Four sites catch AnonPathError and degrade: Files.for_reader retries
        # with a raw-UID fallback, dicomweb/cache.py and tasks/cache_dicomweb.py
        # log and skip, cli/anon.py counts a failure. A traversal must never
        # degrade into any of those.
        assert not issubclass(UnsafePathError, AnonPathError)
