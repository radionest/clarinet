"""Single owner for plan/ custom-code registries.

Three decorator-based registries (schema hydrators, slicer context
hydrators, record-data validators) share one lifecycle: a ``.py`` file in
the project's config folder is imported at startup and its decorators
register callables by name.  ``CustomCodeRegistry`` owns that lifecycle —
``sys.path`` setup, fail-fast import, registry diff and logging — so the
per-domain modules keep only their decorator signatures.
"""

import sys
from collections.abc import Mapping
from pathlib import Path

from clarinet.utils.logger import logger


class CustomCodeRegistry[T]:
    """Name → value registry populated by decorators in a plan/ file.

    Args:
        filename_setting: Name of the ``settings`` attribute holding the
            file location relative to the config folder
            (e.g. ``"config_validators_file"``).
        label: Human-readable singular label for log/error messages.
    """

    def __init__(self, *, filename_setting: str, label: str) -> None:
        self._filename_setting = filename_setting
        self._label = label
        self._items: dict[str, T] = {}

    def register(self, name: str, value: T, *, replace: bool = True) -> None:
        """Register *value* under *name*.

        Raises:
            ValueError: If ``replace=False`` and *name* is already taken.
                Decorators that need a domain-specific duplicate message
                should check :meth:`get` first and raise their own error.
        """
        if not replace and name in self._items:
            raise ValueError(f"{self._label} '{name}' is already registered")
        self._items[name] = value

    def load_from(self, folder: str | Path) -> int:
        """Import the registry's plan/ file from *folder*, fail-fast on errors.

        The file is imported as a ``clarinet_plan.`` submodule off the active
        anchor root — no ``sys.path`` mutation. Decorators in the file register
        callables into this registry as a side effect of the import.

        Returns:
            Number of *new* names registered (0 if the file is absent).

        Raises:
            ConfigLoadError: If the file exists but fails to import, or lives
                outside the active plan root.
        """
        from clarinet.config.plan_package import (
            ensure_plan_root,
            import_plan_module,
            module_name_for,
        )
        from clarinet.settings import settings

        path = Path(folder) / getattr(settings, self._filename_setting)
        if not path.exists():
            return 0

        ensure_plan_root(folder)
        dotted = module_name_for(path)

        before = set(self._items)
        # A cached module returns without re-running decorators — expected to add
        # nothing. Distinguishing this from a genuinely decorator-less file lets
        # us suppress the warning only when it is truly benign (see below).
        cache_hit = dotted in sys.modules
        import_plan_module(dotted, path_hint=path)

        added = set(self._items) - before
        if added:
            logger.info(f"Loaded {len(added)} {self._label}(s): {', '.join(sorted(added))}")
        elif cache_hit and self._items:
            # Re-import of an already-cached module that has already populated
            # this registry: no new names is correct, not degradation.
            pass
        else:
            # The file imported cleanly (fresh import, or a cache hit against an
            # *empty* registry — exactly the #352 silent-degradation shape) yet
            # added nothing. Likely a missing decorator — make it visible.
            logger.warning(
                f"{path} imported successfully but registered no new {self._label}(s) — "
                f"check that the decorators are present"
            )
        return len(added)

    def get(self, name: str) -> T | None:
        return self._items.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._items)

    def clear(self) -> None:
        self._items.clear()

    def snapshot(self) -> dict[str, T]:
        """Copy of the current mapping — pair with :meth:`restore` in test fixtures."""
        return dict(self._items)

    def restore(self, items: Mapping[str, T]) -> None:
        """Replace the registry contents with *items* — pair with :meth:`snapshot`."""
        self._items = dict(items)
