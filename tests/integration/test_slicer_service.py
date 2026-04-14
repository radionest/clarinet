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


def test_build_context_block(slicer_service: SlicerService) -> None:
    """Unit test: context block generation (no Slicer required)."""
    context = {
        "working_folder": "/tmp/test",
        "count": 42,
        "flag": True,
        "values": [1, 2, 3],
    }
    block = slicer_service._build_context_block(context)

    assert "working_folder = '/tmp/test'" in block
    assert "count = 42" in block
    assert "flag = True" in block
    assert "values = [1, 2, 3]" in block


def test_build_script_with_context(slicer_service: SlicerService) -> None:
    """Unit test: full script assembly (no Slicer required)."""
    script = "print('user code')"
    context = {"x": 10}
    full = slicer_service._build_script(script, context)

    # Helper source should be at the start
    assert "class SlicerHelper" in full
    # Context should appear
    assert "x = 10" in full
    # User script wrapped in _run()
    assert "    print('user code')" in full
    assert "def _run():" in full


def test_build_script_without_context(slicer_service: SlicerService) -> None:
    """Unit test: script without context skips context block."""
    script = "print('no context')"
    full = slicer_service._build_script(script, None)

    assert "# --- context variables ---" not in full
    assert "    print('no context')" in full


def test_build_script_run_can_access_helper_names(slicer_service: SlicerService) -> None:
    """Regression: _run() must resolve SlicerHelper even if user script shadows the name.

    User scripts like ``SlicerHelper = SlicerHelper(working_folder)`` make
    the name local to _run() without explicit ``global`` declarations,
    causing UnboundLocalError. The global declarations added by
    _build_script() prevent this.
    """
    # Simulate the pattern that triggers UnboundLocalError without the fix:
    # assignment to SlicerHelper inside _run() would make it local.
    script = "SlicerHelper = SlicerHelper\n__execResult = {'ok': True}"
    full = slicer_service._build_script(script, {"working_folder": "/tmp"})

    ns: dict = {}
    exec(full, ns)  # would raise UnboundLocalError without global declarations
    assert ns["__execResult"]["ok"] is True


def test_build_script_run_can_access_nested_imports(slicer_service: SlicerService) -> None:
    """Regression: slicer/qt/vtk/ctk must be declared global in _run().

    These names are assigned inside try/except ImportError in helper.py,
    so ast.iter_child_nodes() misses them. Without explicit global
    declarations, ``slicer = SlicerHelper(...)`` makes ``slicer`` local
    and any prior ``slicer.*`` call raises UnboundLocalError.
    """
    script = "slicer = slicer\n__execResult = {'ok': True}"
    full = slicer_service._build_script(script, {"working_folder": "/tmp"})

    ns: dict = {}
    exec(full, ns)
    assert ns["__execResult"]["ok"] is True
