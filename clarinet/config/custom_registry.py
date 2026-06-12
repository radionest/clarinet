"""Single owner for plan/ custom-code registries.

Three decorator-based registries (schema hydrators, slicer context
hydrators, record-data validators) share one lifecycle: a ``.py`` file in
the project's config folder is imported at startup and its decorators
register callables by name.  ``CustomCodeRegistry`` owns that lifecycle —
``sys.path`` setup, fail-fast import, registry diff and logging — so the
per-domain modules keep only their decorator signatures.
"""

from collections.abc import Mapping
from pathlib import Path

from clarinet.config.python_loader import config_sys_path, load_module_from_file
from clarinet.utils.logger import logger


class CustomCodeRegistry[T]:
    """Name → value registry populated by decorators in a plan/ file.

    Args:
        filename_setting: Name of the ``settings`` attribute holding the
            file location relative to the config folder
            (e.g. ``"config_validators_file"``).
        module_name: ``sys.modules`` name used while importing the file.
        label: Human-readable singular label for log/error messages.
    """

    def __init__(self, *, filename_setting: str, module_name: str, label: str) -> None:
        self._filename_setting = filename_setting
        self._module_name = module_name
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

        Puts *folder* (the config root) and the file's parent on ``sys.path``
        for the duration of the import, so the file can use both package
        imports from the root (``from utils.x import y``) and sibling
        imports — regardless of which subdirectory it lives in.

        Returns:
            Number of *new* names registered (0 if the file is absent).

        Raises:
            ConfigLoadError: If the file exists but fails to import.
        """
        from clarinet.settings import settings

        path = Path(folder) / getattr(settings, self._filename_setting)
        if not path.exists():
            return 0

        before = set(self._items)
        with config_sys_path(Path(folder), path.parent):
            load_module_from_file(self._module_name, path)

        added = set(self._items) - before
        if added:
            logger.info(f"Loaded {len(added)} {self._label}(s): {', '.join(sorted(added))}")
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
