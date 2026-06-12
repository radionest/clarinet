"""Tests for plan/ custom-code loading primitives and CustomCodeRegistry.

Covers:
- config_sys_path: insertion order, dedup against existing entries, restore
- load_module_from_file: fail-fast ConfigLoadError, sys.modules hygiene
- CustomCodeRegistry: register/get/names/clear, replace=False, load_from
- load_python_config: broken record_types.py / files_catalog.py crash startup
"""

import sys
import textwrap

import pytest

import clarinet.config.custom_registry as custom_registry_module
from clarinet.config.custom_registry import CustomCodeRegistry
from clarinet.config.python_loader import (
    config_sys_path,
    load_module_from_file,
    load_python_config,
)
from clarinet.exceptions.domain import ConfigLoadError

# ---------------------------------------------------------------------------
# config_sys_path
# ---------------------------------------------------------------------------


class TestConfigSysPath:
    def test_inserts_and_restores(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        a_str, b_str = str(a.resolve()), str(b.resolve())

        before = list(sys.path)
        with config_sys_path(a, b):
            assert a_str in sys.path
            assert b_str in sys.path
            # Args are low-priority-first: the last one wins module lookup
            assert sys.path.index(b_str) < sys.path.index(a_str)
        assert sys.path == before

    def test_existing_entry_not_duplicated_or_removed(self, tmp_path, monkeypatch):
        d = tmp_path / "d"
        d.mkdir()
        d_str = str(d.resolve())
        monkeypatch.syspath_prepend(d_str)

        before = list(sys.path)
        with config_sys_path(d):
            assert sys.path.count(d_str) == 1
        # Entry owned by the caller survives the context manager
        assert sys.path == before

    def test_duplicate_args_inserted_once(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        d_str = str(d.resolve())

        with config_sys_path(d, d):
            assert sys.path.count(d_str) == 1
        assert d_str not in sys.path

    def test_restores_on_exception(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        d_str = str(d.resolve())

        with pytest.raises(RuntimeError), config_sys_path(d):
            raise RuntimeError("boom")
        assert d_str not in sys.path


# ---------------------------------------------------------------------------
# load_module_from_file
# ---------------------------------------------------------------------------


class TestLoadModuleFromFile:
    def test_loads_module_and_pops_by_default(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("X = 1\n")

        module = load_module_from_file("clarinet_test_mod", f)

        assert module.X == 1
        assert "clarinet_test_mod" not in sys.modules

    def test_keep_in_sys_leaves_module(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("X = 2\n")

        try:
            load_module_from_file("clarinet_test_keep", f, keep_in_sys=True)
            assert "clarinet_test_keep" in sys.modules
        finally:
            sys.modules.pop("clarinet_test_keep", None)

    def test_broken_file_raises_and_pops_sys_modules(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("raise RuntimeError('boom')\n")

        with pytest.raises(ConfigLoadError) as exc_info:
            load_module_from_file("clarinet_test_broken", f)

        # The half-initialized module must not leak into later imports
        assert "clarinet_test_broken" not in sys.modules
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert exc_info.value.path == str(f)

    def test_syntax_error_raises(self, tmp_path):
        f = tmp_path / "syntax.py"
        f.write_text("def broken(\n")

        with pytest.raises(ConfigLoadError):
            load_module_from_file("clarinet_test_syntax", f)
        assert "clarinet_test_syntax" not in sys.modules


# ---------------------------------------------------------------------------
# CustomCodeRegistry
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(monkeypatch):
    """Fresh registry, reachable from loaded plan-files via a module attribute.

    Files loaded by ``load_from`` cannot reference test-local objects, so the
    instance is exposed as ``clarinet.config.custom_registry._TEST_REGISTRY``
    for the duration of the test (monkeypatch removes it afterwards).
    """
    reg: CustomCodeRegistry[object] = CustomCodeRegistry(
        filename_setting="config_validators_file",
        module_name="clarinet_test_registry_module",
        label="test item",
    )
    monkeypatch.setattr(custom_registry_module, "_TEST_REGISTRY", reg, raising=False)
    return reg


_REGISTERING_FILE = textwrap.dedent("""\
    from clarinet.config.custom_registry import _TEST_REGISTRY

    _TEST_REGISTRY.register("loaded.one", 1)
    _TEST_REGISTRY.register("loaded.two", 2)
    """)


class TestCustomCodeRegistry:
    def test_register_get_names_clear(self, registry):
        registry.register("a", 1)
        registry.register("b", 2)

        assert registry.get("a") == 1
        assert registry.get("missing") is None
        assert registry.names() == frozenset({"a", "b"})

        registry.clear()
        assert registry.names() == frozenset()

    def test_register_replace_true_overwrites(self, registry):
        registry.register("dup", 1)
        registry.register("dup", 2)
        assert registry.get("dup") == 2

    def test_register_replace_false_raises_on_duplicate(self, registry):
        registry.register("dup", 1, replace=False)
        with pytest.raises(ValueError, match="already registered"):
            registry.register("dup", 2, replace=False)
        assert registry.get("dup") == 1

    def test_snapshot_restore(self, registry):
        registry.register("a", 1)
        saved = registry.snapshot()

        registry.clear()
        registry.register("b", 2)
        registry.restore(saved)

        assert registry.names() == frozenset({"a"})

    def test_load_from_missing_file_returns_zero(self, registry, tmp_path):
        assert registry.load_from(tmp_path) == 0
        assert registry.load_from(tmp_path / "nonexistent") == 0

    def test_load_from_registers_and_cleans_up(self, registry, tmp_path):
        (tmp_path / "validators.py").write_text(_REGISTERING_FILE)

        sys_path_before = list(sys.path)
        count = registry.load_from(tmp_path)

        assert count == 2
        assert registry.names() == frozenset({"loaded.one", "loaded.two"})
        assert sys.path == sys_path_before
        assert "clarinet_test_registry_module" not in sys.modules

    def test_load_from_counts_only_new_names(self, registry, tmp_path):
        registry.register("loaded.one", 0)
        (tmp_path / "validators.py").write_text(_REGISTERING_FILE)

        count = registry.load_from(tmp_path)

        assert count == 1  # "loaded.one" was already present

    def test_load_from_zero_new_registrations_warns(self, registry, tmp_path, monkeypatch):
        """A file that imports cleanly but registers nothing (missing decorator)
        is the same silent-degradation class as a swallowed import error."""
        registry.register("loaded.one", 0)
        registry.register("loaded.two", 0)
        (tmp_path / "validators.py").write_text(_REGISTERING_FILE)

        captured: list[str] = []
        monkeypatch.setattr(
            custom_registry_module.logger,
            "warning",
            lambda msg, *a, **kw: captured.append(str(msg)),
        )

        assert registry.load_from(tmp_path) == 0
        assert any("registered no new" in m for m in captured)

    def test_load_from_broken_file_raises(self, registry, tmp_path):
        (tmp_path / "validators.py").write_text("raise RuntimeError('import error')\n")

        sys_path_before = list(sys.path)
        with pytest.raises(ConfigLoadError):
            registry.load_from(tmp_path)
        assert sys.path == sys_path_before


# ---------------------------------------------------------------------------
# load_python_config fail-fast (was: silent ``return []``)
# ---------------------------------------------------------------------------


class TestLoadPythonConfigFailFast:
    @pytest.mark.asyncio
    async def test_broken_record_types_raises(self, tmp_path):
        (tmp_path / "record_types.py").write_text("raise RuntimeError('broken config')\n")

        with pytest.raises(ConfigLoadError):
            await load_python_config(tmp_path)

    @pytest.mark.asyncio
    async def test_broken_files_catalog_raises(self, tmp_path):
        (tmp_path / "files_catalog.py").write_text("raise RuntimeError('broken catalog')\n")
        (tmp_path / "record_types.py").write_text(
            textwrap.dedent("""\
            from clarinet.config.primitives import RecordDef

            rt = RecordDef(name="rt", level="SERIES")
            """)
        )

        with pytest.raises(ConfigLoadError):
            await load_python_config(tmp_path)

    @pytest.mark.asyncio
    async def test_files_catalog_in_custom_subdirectory_sibling_import(self, tmp_path, monkeypatch):
        """files_catalog.py in its own subdirectory must be able to import its
        siblings — the loader puts the catalog's parent on sys.path too."""
        from clarinet.settings import settings

        (tmp_path / "catalog").mkdir()
        (tmp_path / "catalog" / "helper_defs.py").write_text("PATTERN = 'seg.nrrd'\n")
        (tmp_path / "catalog" / "files_catalog.py").write_text(
            textwrap.dedent("""\
            from helper_defs import PATTERN

            from clarinet.config.primitives import FileDef

            seg = FileDef(pattern=PATTERN, level="SERIES")
            """)
        )
        (tmp_path / "record_types.py").write_text(
            textwrap.dedent("""\
            from clarinet.config.primitives import RecordDef

            rt = RecordDef(name="rt-catalog-subdir", level="SERIES")
            """)
        )

        monkeypatch.setattr(settings, "config_files_catalog_file", "catalog/files_catalog.py")
        monkeypatch.delitem(sys.modules, "helper_defs", raising=False)
        try:
            items = await load_python_config(tmp_path)
        finally:
            sys.modules.pop("helper_defs", None)

        assert [item.name for item in items] == ["rt-catalog-subdir"]
