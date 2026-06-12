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
# clarinet_plan anchor package (plan_package.py)
# ---------------------------------------------------------------------------


class TestPlanPackage:
    """Anchor-package machinery: activate/ensure/deactivate, name derivation,
    import classification.  The autouse ``_plan_package_sanitation`` conftest
    fixture tears the anchor down after each test."""

    def _make_root(self, tmp_path):
        (tmp_path / "record_types.py").write_text("MARKER = 'rt'\n")
        (tmp_path / "utils").mkdir()
        (tmp_path / "utils" / "study_type.py").write_text("VALUE = 7\n")
        return tmp_path

    def test_activate_installs_anchor(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        pp.activate_plan_package(root)
        assert pp.plan_root() == root.resolve()
        assert pp.PLAN_PACKAGE in sys.modules

    def test_reactivation_replaces_stale_anchor(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        pp.activate_plan_package(root)
        pp.import_plan_module("clarinet_plan.utils")
        assert hasattr(sys.modules[pp.PLAN_PACKAGE], "utils")

        pp.activate_plan_package(root)
        # Fresh anchor: the stale submodule attribute does not survive.
        assert not hasattr(sys.modules[pp.PLAN_PACKAGE], "utils")

    def test_ensure_root_noop_for_root_and_descendants(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        pp.activate_plan_package(root)
        pp.ensure_plan_root(root)
        pp.ensure_plan_root(root / "utils")
        assert pp.plan_root() == root.resolve()

    def test_ensure_root_auto_activates_when_absent(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        assert pp.plan_root() is None
        pp.ensure_plan_root(root)
        assert pp.plan_root() == root.resolve()

    def test_ensure_root_outside_raises(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = tmp_path / "plan"
        root.mkdir()
        self._make_root(root)
        pp.activate_plan_package(root)
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        with pytest.raises(ConfigLoadError, match="must live inside config_tasks_path"):
            pp.ensure_plan_root(outside)

    def test_one_file_one_name(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        pp.activate_plan_package(root)
        assert pp.module_name_for(root / "record_types.py") == "clarinet_plan.record_types"
        # A file in a subdirectory is reachable only as clarinet_plan.<sub>.<mod>.
        assert (
            pp.module_name_for(root / "utils" / "study_type.py") == "clarinet_plan.utils.study_type"
        )

    def test_relative_imports_work(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        (root / "workflows").mkdir()
        (root / "workflows" / "callbacks.py").write_text("CB = 'cb'\n")
        (root / "workflows" / "ct_flow.py").write_text(
            "from .callbacks import CB\nfrom ..utils.study_type import VALUE\nOUT = (CB, VALUE)\n"
        )
        pp.activate_plan_package(root)
        mod = pp.import_plan_module("clarinet_plan.workflows.ct_flow")
        assert mod.OUT == ("cb", 7)

    def test_cross_module_import_late_from_early(self, tmp_path):
        """Cross-flow imports work in both directions — the sorted-order
        limitation of the old loader is gone (native module cache)."""
        from clarinet.config import plan_package as pp

        root = tmp_path
        # a_flow sorts BEFORE b_flow yet imports it — re-execution-free.
        (root / "a_flow.py").write_text("from clarinet_plan.b_flow import B\nA = B + 1\n")
        (root / "b_flow.py").write_text("B = 100\n")
        pp.activate_plan_package(root)
        mod = pp.import_plan_module("clarinet_plan.a_flow")
        assert mod.A == 101

    def test_invalid_identifier_filename_raises(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        (root / "2_phase_flow.py").write_text("X = 1\n")
        pp.activate_plan_package(root)
        with pytest.raises(ConfigLoadError) as exc:
            pp.module_name_for(root / "2_phase_flow.py")
        assert "2_phase_flow" in str(exc.value)

    def test_invalid_identifier_dirname_raises(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        (root / "my-utils").mkdir()
        (root / "my-utils" / "helper.py").write_text("X = 1\n")
        pp.activate_plan_package(root)
        with pytest.raises(ConfigLoadError) as exc:
            pp.module_name_for(root / "my-utils" / "helper.py")
        assert "my-utils" in str(exc.value)

    def test_keyword_segment_raises(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        (root / "class.py").write_text("X = 1\n")
        pp.activate_plan_package(root)
        with pytest.raises(ConfigLoadError):
            pp.module_name_for(root / "class.py")

    def test_module_vs_dir_conflict_raises(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        (root / "hydrators.py").write_text("X = 1\n")
        (root / "hydrators").mkdir()
        pp.activate_plan_package(root)
        with pytest.raises(ConfigLoadError, match="collision"):
            pp.module_name_for(root / "hydrators.py")

    def test_module_name_outside_root_raises(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = tmp_path / "plan"
        root.mkdir()
        pp.activate_plan_package(root)
        (tmp_path / "elsewhere.py").write_text("X = 1\n")
        with pytest.raises(ConfigLoadError, match="outside"):
            pp.module_name_for(tmp_path / "elsewhere.py")

    def test_migration_hint_for_unprefixed_sibling_import(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        (root / "tasks.py").write_text("from record_types import MARKER\n")
        pp.activate_plan_package(root)
        with pytest.raises(ConfigLoadError) as exc:
            pp.import_plan_module("clarinet_plan.tasks")
        msg = str(exc.value)
        assert "from clarinet_plan.record_types import" in msg

    def test_third_party_missing_not_reported_as_module_not_found(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        (root / "needs_dep.py").write_text("import a_totally_absent_third_party\n")
        pp.activate_plan_package(root)
        with pytest.raises(ConfigLoadError) as exc:
            pp.import_plan_module("clarinet_plan.needs_dep")
        msg = str(exc.value)
        # A failed transitive import is an import-failure, not "plan module not found".
        assert "plan module" not in msg
        assert "a_totally_absent_third_party" in msg

    def test_guard_against_installed_distribution(self, tmp_path, monkeypatch):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        fake_spec = pp.ModuleSpec("clarinet_plan", None)
        monkeypatch.setattr(
            pp.PathFinder, "find_spec", lambda name, path=None, target=None: fake_spec
        )
        with pytest.raises(ConfigLoadError, match="distribution is installed"):
            pp.activate_plan_package(root)

    def test_sys_path_untouched(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        before = list(sys.path)
        pp.activate_plan_package(root)
        pp.import_plan_module("clarinet_plan.record_types")
        pp.ensure_plan_root(root / "utils")
        assert sys.path == before
        assert str(root.resolve()) not in sys.path

    def test_deactivate_purges_anchor(self, tmp_path):
        from clarinet.config import plan_package as pp

        root = self._make_root(tmp_path)
        pp.activate_plan_package(root)
        pp.import_plan_module("clarinet_plan.utils")
        pp.deactivate_plan_package()
        assert pp.plan_root() is None
        assert "clarinet_plan" not in sys.modules
        assert "clarinet_plan.utils" not in sys.modules


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

    def test_load_from_registers_and_caches(self, registry, tmp_path):
        (tmp_path / "validators.py").write_text(_REGISTERING_FILE)

        sys_path_before = list(sys.path)
        count = registry.load_from(tmp_path)

        assert count == 2
        assert registry.names() == frozenset({"loaded.one", "loaded.two"})
        # No sys.path mutation; the file is cached as a clarinet_plan submodule.
        assert sys.path == sys_path_before
        assert "clarinet_plan.validators" in sys.modules

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

    def test_load_from_cache_hit_empty_registry_warns(self, registry, tmp_path, monkeypatch):
        """Cache hit against an EMPTY registry is the #352 silent-degradation
        shape (a fixture cleared the registry while the module stayed cached)
        — it must still warn."""
        (tmp_path / "validators.py").write_text(_REGISTERING_FILE)
        assert registry.load_from(tmp_path) == 2  # fresh import: populate + cache

        registry.clear()  # registry emptied, module remains in sys.modules

        captured: list[str] = []
        monkeypatch.setattr(
            custom_registry_module.logger,
            "warning",
            lambda msg, *a, **kw: captured.append(str(msg)),
        )
        assert registry.load_from(tmp_path) == 0  # cache hit, nothing re-runs
        assert any("registered no new" in m for m in captured)

    def test_load_from_cache_hit_nonempty_registry_silent(self, registry, tmp_path, monkeypatch):
        """Cache hit against a NON-empty registry is benign — no warning."""
        (tmp_path / "validators.py").write_text(_REGISTERING_FILE)
        assert registry.load_from(tmp_path) == 2  # populate + cache

        captured: list[str] = []
        monkeypatch.setattr(
            custom_registry_module.logger,
            "warning",
            lambda msg, *a, **kw: captured.append(str(msg)),
        )
        assert registry.load_from(tmp_path) == 0  # cache hit, registry still full
        assert not captured

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
    async def test_files_catalog_in_custom_subdirectory_package_import(self, tmp_path, monkeypatch):
        """files_catalog.py in its own subdirectory imports a sibling via the
        ``clarinet_plan.<subdir>`` package path — no sys.path entry needed."""
        from clarinet.settings import settings

        (tmp_path / "catalog").mkdir()
        (tmp_path / "catalog" / "helper_defs.py").write_text("PATTERN = 'seg.nrrd'\n")
        (tmp_path / "catalog" / "files_catalog.py").write_text(
            textwrap.dedent("""\
            from clarinet_plan.catalog.helper_defs import PATTERN

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
        # The autouse _plan_package_sanitation fixture purges clarinet_plan.* afterwards.
        items = await load_python_config(tmp_path)

        assert [item.name for item in items] == ["rt-catalog-subdir"]
