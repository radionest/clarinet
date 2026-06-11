"""Integration tests for SlicerService — require a running 3D Slicer instance."""

import pytest

from clarinet.services.slicer.service import SlicerService

pytestmark = [
    pytest.mark.slicer,
    pytest.mark.asyncio,
    pytest.mark.usefixtures("_check_slicer"),
    pytest.mark.xdist_group("slicer"),
]


async def test_execute_with_helper(slicer_service: SlicerService, slicer_url: str) -> None:
    """Script with prepended helper should have SlicerHelper available."""
    script = "print(type(SlicerHelper).__name__)"
    result = await slicer_service.execute(slicer_url, script)
    assert isinstance(result, dict)


async def test_execute_with_context(slicer_service: SlicerService, slicer_url: str) -> None:
    """Context variables should be accessible in the script."""
    script = "print(my_var)"
    context = {"my_var": "hello_from_context"}
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_execute_raw(slicer_service: SlicerService, slicer_url: str) -> None:
    """Raw execution should not prepend helper code."""
    result = await slicer_service.execute_raw(slicer_url, "print('raw')")
    assert isinstance(result, dict)


async def test_ping(slicer_service: SlicerService, slicer_url: str) -> None:
    """Ping should succeed against a running Slicer."""
    assert await slicer_service.ping(slicer_url) is True


def test_build_script_with_context(slicer_service: SlicerService) -> None:
    """Unit test: full script assembly (no Slicer required)."""
    script = "print('user code')"
    context = {"x": 10}
    full = slicer_service._build_script(script, context)

    # Helper source should be at the start
    assert "class SlicerHelper" in full
    # Context injected into namespace
    assert "_ns['x'] = 10" in full
    # User script compiled and exec'd
    assert "exec(compile(" in full
    assert "def _run():" in full


def test_build_script_without_context(slicer_service: SlicerService) -> None:
    """Unit test: script without context skips context block."""
    script = "print('no context')"
    full = slicer_service._build_script(script, None)

    assert "_ns[" not in full
    assert "exec(compile(" in full


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
