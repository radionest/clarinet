"""Unit tests for SlicerService._build_script — no Slicer required.

The generated script is exec'd in a plain dict standing in for Slicer's
reused exec() namespace; helper.py's ``_Dummy`` stubs make the prepended
helper source executable outside Slicer.

These tests deliberately live in root ``tests/`` rather than
``tests/integration/test_slicer_service.py``: that module is gated behind
the ``_check_slicer`` session fixture, so anything placed there silently
skips in CI.
"""

import pytest

from clarinet.services.slicer.service import SlicerService


@pytest.fixture
def slicer_service() -> SlicerService:
    return SlicerService()


def test_build_script_with_context(slicer_service: SlicerService) -> None:
    """Full script assembly: helper + injection into module globals + cleanup."""
    script = "print('user code')"
    context = {"x": 10}
    full = slicer_service._build_script(script, context)

    # Helper source should be at the start
    assert "class SlicerHelper" in full
    # Context injected into module globals (visible to helper functions)
    assert "globals()['x'] = 10" in full
    # ...and removed after the run
    assert "for _k in ('x',):" in full
    assert "globals().pop(_k, None)" in full
    # User script compiled and exec'd
    assert "exec(compile(" in full
    assert "def _run():" in full


def test_build_script_without_context(slicer_service: SlicerService) -> None:
    """Script without context skips injection; the cleanup loop is a no-op."""
    script = "print('no context')"
    full = slicer_service._build_script(script, None)

    assert "for _k in ():" in full
    assert "exec(compile(" in full


def test_context_visible_to_user_script_and_helpers(slicer_service: SlicerService) -> None:
    """Injection lands in helper functions' ``__globals__`` AND in the user script.

    ``_get_pacs_helper`` reads injected PACS params via ``globals()`` — its
    ``__globals__`` is the module namespace, not the per-call ``_ns``. The
    user script sees the same values through the ``_ns = dict(globals())``
    copy, built after the injection.
    """
    script = (
        "__execResult = {"
        "'user_view': pacs_host, "
        "'helper_view': _get_pacs_helper.__globals__.get('pacs_host')"
        "}"
    )
    full = slicer_service._build_script(script, {"pacs_host": "ctx-host"})
    ns: dict = {}
    exec(full, ns)
    assert ns["__execResult"] == {"user_view": "ctx-host", "helper_view": "ctx-host"}


def test_context_keys_removed_after_run(slicer_service: SlicerService) -> None:
    """Injected keys must not persist in the reused exec namespace.

    Slicer reuses one namespace for all calls — anything left behind bleeds
    into the next script (e.g. a STUDY-level record silently picking up the
    previous record's ``series_uid``).
    """
    script = "__execResult = {'saw': pacs_host}"
    full = slicer_service._build_script(script, {"pacs_host": "ctx-host", "record_id": 42})
    ns: dict = {}
    exec(full, ns)
    assert ns["__execResult"] == {"saw": "ctx-host"}
    assert "pacs_host" not in ns
    assert "record_id" not in ns


def test_context_keys_removed_when_user_script_raises(slicer_service: SlicerService) -> None:
    """Cleanup runs in ``finally`` — a crashing user script must not leak context."""
    script = "raise RuntimeError('boom')"
    full = slicer_service._build_script(script, {"pacs_host": "ctx-host"})
    ns: dict = {}
    with pytest.raises(RuntimeError, match="boom"):
        exec(full, ns)
    assert "pacs_host" not in ns


def test_execresult_not_carried_over_between_calls(slicer_service: SlicerService) -> None:
    """Stale ``__execResult`` must not bleed across calls in Slicer's reused namespace.

    Slicer keeps one exec namespace for every HTTP call. A script that assigns
    ``__execResult`` writes it into that namespace; a *later* script that assigns
    none must still return ``{}`` (the documented ``execute()`` contract), not the
    previous call's result. Regression for #339 — worst case is a
    ``slicer_result_validator`` that forgets to assign one silently merging another
    record's validator output into ``record.data``.
    """
    ns: dict = {}
    # Call 1 assigns a result — the propagation line writes it into the reused ns.
    exec(slicer_service._build_script("__execResult = {'first': True}", None), ns)
    assert ns["__execResult"] == {"first": True}
    # Call 2 in the SAME namespace assigns nothing — must reset to {}, not carry over.
    exec(slicer_service._build_script("pass", None), ns)
    assert ns["__execResult"] == {}


def test_build_script_user_can_shadow_helper_names(slicer_service: SlicerService) -> None:
    """Regression: user scripts must be able to shadow any helper name.

    Patterns like ``slicer = SlicerHelper(working_folder)`` or
    ``SlicerHelper = SlicerHelper(...)`` must not cause UnboundLocalError.
    The inner exec() runs in a flat namespace (dict as both globals and
    locals), so there is no function-scope local-vs-global distinction.
    """
    # Shadow a top-level class (previously caught by AST extraction)
    script = "SlicerHelper = SlicerHelper\n__execResult = {'ok': True}"
    full = slicer_service._build_script(script, {"working_folder": "/tmp"})
    ns: dict = {}
    exec(full, ns)
    assert ns["__execResult"]["ok"] is True

    # Shadow a nested import (slicer/qt/vtk/ctk — missed by AST extraction,
    # the original production bug)
    script = "slicer = slicer\n__execResult = {'ok': True}"
    full = slicer_service._build_script(script, {"working_folder": "/tmp"})
    ns = {}
    exec(full, ns)
    assert ns["__execResult"]["ok"] is True

    # Read-before-write pattern: the actual trigger for UnboundLocalError
    script = "x = type(slicer).__name__\nslicer = 42\n__execResult = {'x': x}"
    full = slicer_service._build_script(script, None)
    ns = {}
    exec(full, ns)
    assert ns["__execResult"]["x"] == "_Dummy"
