"""Anchor-package machinery for loading ``plan/`` custom code.

The downstream project's config folder (``settings.config_tasks_path``) is
exposed to Python as a single in-memory namespace package, ``clarinet_plan``,
whose ``__path__`` points at that one root.  Every ``plan/`` file is an
ordinary submodule imported **only** from the root::

    from clarinet_plan.record_types import master_model
    from clarinet_plan.utils.study_type import classify
    from clarinet_plan.workflows.ct_flow import ...
    from .callbacks import notify        # relative imports also work

No directory is ever placed on ``sys.path``.  The whole class of bug "wrong
directory on ``sys.path``" is therefore inexpressible, stdlib shadowing by a
``plan/logging.py`` cannot happen, and a file has exactly one dotted name (no
double registration).  ``exactly-once`` execution comes for free from Python's
native module cache, so cross-flow imports work in both directions.

Lifecycle (see ``.claude/rules/custom-code-loading.md``):

* ``activate_plan_package(root)`` — startup / worker entry; installs a fresh
  anchor rooted at *root*.
* ``ensure_plan_root(folder)`` — first line of every loader; self-activates for
  direct test calls and rejects folders outside the active root.
* ``deactivate_plan_package()`` — test sanitation (autouse teardown fixture).
* ``module_name_for(path)`` / ``import_plan_module(dotted)`` — path → canonical
  dotted name (validation lives here) and import-with-error-classification.

Threading invariant: ``activate_plan_package`` / ``ensure_plan_root`` /
``deactivate_plan_package`` mutate global import state and must be called only
from application startup or test setup — never from request handlers or
background tasks.

Running a ``plan/`` file directly (``python plan/validators.py``) is **not**
supported — there is no anchor in that process.  Relative imports
(``from .record_types import X``, ``from ..utils.study_type import y``) are a
fully supported alternative to the ``clarinet_plan.``-prefixed form.
"""

from __future__ import annotations

import importlib
import importlib.util
import keyword
import sys
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path
from types import ModuleType

from clarinet.exceptions.domain import ConfigLoadError

PLAN_PACKAGE = "clarinet_plan"


def _purge_modules() -> None:
    """Remove the anchor and every submodule from ``sys.modules``.

    Exact-prefix match (``k == PLAN_PACKAGE or k.startswith(PLAN_PACKAGE + ".")``)
    so a hypothetical ``clarinet_planner`` is never touched.  The stale anchor
    object holds submodule attributes (``from clarinet_plan import utils`` would
    return ``clarinet_plan.utils`` without re-import), so the anchor is always
    recreated, never reused — the "purge submodules, keep the anchor" variant is
    intentionally not implemented anywhere.
    """
    for key in [
        k for k in list(sys.modules) if k == PLAN_PACKAGE or k.startswith(PLAN_PACKAGE + ".")
    ]:
        sys.modules.pop(key, None)


def activate_plan_package(root: str | Path) -> None:
    """Install a fresh ``clarinet_plan`` anchor rooted at *root*.

    Full cycle: guard against a real installed distribution → purge any previous
    anchor + submodules → create a fresh anchor whose ``__path__`` is
    ``[resolve(root)]`` → ``importlib.invalidate_caches()``.

    Raises:
        ConfigLoadError: If a real ``clarinet_plan`` distribution is importable
            from ``sys.path`` (it would make the in-memory anchor ambiguous).
    """
    resolved = Path(root).resolve()

    real = PathFinder.find_spec(PLAN_PACKAGE, None)
    if real is not None:
        raise ConfigLoadError(
            f"a real '{PLAN_PACKAGE}' distribution is installed "
            f"(origin: {real.origin}); clarinet uses '{PLAN_PACKAGE}' as an "
            f"in-memory anchor for the project config folder and cannot coexist "
            f"with an installed package of the same name — rename or uninstall it."
        )

    _purge_modules()

    spec = ModuleSpec(PLAN_PACKAGE, None, is_package=True)
    # Set the root in spec.submodule_search_locations BEFORE module_from_spec so
    # module.__path__ and module.__spec__.submodule_search_locations stay in sync
    # (a post-hoc ``mod.__path__ = ...`` leaves the spec's list empty, desyncing
    # find_spec / pkgutil). Subpackages without __init__.py work as namespace
    # portions off this single root.
    spec.submodule_search_locations = [str(resolved)]
    anchor = importlib.util.module_from_spec(spec)
    sys.modules[PLAN_PACKAGE] = anchor

    importlib.invalidate_caches()


def ensure_plan_root(folder: str | Path) -> None:
    """Ensure the anchor is active and *folder* lives under its root.

    * No anchor yet → ``activate_plan_package(folder)`` (self-activation for
      direct test calls).
    * Anchor active and *folder* == root or a descendant of root → no-op, but
      ``importlib.invalidate_caches()`` **unconditionally** (a directory created
      after activation may carry a negative entry in
      ``sys.path_importer_cache``).
    * Anchor active and *folder* outside root → ``ConfigLoadError`` (explicit
      failure, never a silent reactivation; test isolation goes through the
      ``deactivate_plan_package`` fixture).

    Raises:
        ConfigLoadError: If *folder* is outside the active root.
    """
    requested = Path(folder).resolve()
    current = plan_root()
    if current is None:
        activate_plan_package(requested)
        return

    if requested == current or current in requested.parents:
        importlib.invalidate_caches()
        return

    raise ConfigLoadError(
        f"plan root mismatch: the '{PLAN_PACKAGE}' anchor is rooted at {current}, "
        f"but a loader was asked to import from {requested}, which is not inside "
        f"it. recordflow_paths must live inside config_tasks_path ({current}). "
        f"(In tests, reset with deactivate_plan_package().)"
    )


def deactivate_plan_package() -> None:
    """Purge the anchor and all submodules — test sanitation."""
    _purge_modules()
    importlib.invalidate_caches()


def plan_root() -> Path | None:
    """Return the currently active plan root, or ``None`` if no anchor exists.

    Used for error messages and migration hints.
    """
    anchor = sys.modules.get(PLAN_PACKAGE)
    if anchor is None:
        return None
    spec = getattr(anchor, "__spec__", None)
    locations = list(getattr(spec, "submodule_search_locations", None) or [])
    return Path(locations[0]) if locations else None


def module_name_for(path: Path) -> str:
    """Map a file/dir *path* to its canonical ``clarinet_plan.*`` dotted name.

    Validation lives here: every path segment between the root and *path* must
    be a valid Python identifier that is not a keyword, because the dotted name
    is used verbatim in ``import`` statements (``class`` passes ``isidentifier``
    but ``from clarinet_plan.class import ...`` is a ``SyntaxError``).  Errors
    name the *filesystem* path so the operator knows which file/dir to rename.

    Module-vs-directory pre-flight: if both ``{seg}.py`` and ``{seg}/`` exist
    under the same parent, raise — ``FileFinder`` silently prefers one and the
    other becomes unimportable.

    Raises:
        ConfigLoadError: *path* outside the root, a non-identifier / keyword
            segment, or a module/directory name collision.
    """
    root = plan_root()
    if root is None:
        raise ConfigLoadError(
            f"'{PLAN_PACKAGE}' is not active — cannot derive a module name for "
            f"{path}. Call activate_plan_package(config_tasks_path) first."
        )

    resolved = Path(path).resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        raise ConfigLoadError(
            f"{resolved} is outside the '{PLAN_PACKAGE}' root {root}; plan files "
            f"must live inside config_tasks_path."
        ) from None

    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]

    if not parts:
        raise ConfigLoadError(
            f"{resolved} resolves to the '{PLAN_PACKAGE}' root itself — no module name to derive."
        )

    for seg in parts:
        if not seg.isidentifier() or keyword.iskeyword(seg):
            raise ConfigLoadError(
                f"cannot import {resolved}: path segment '{seg}' is not a valid "
                f"Python identifier — rename it (no dashes, no leading digit, not "
                f"a Python keyword) so it imports as '{PLAN_PACKAGE}.{seg}'."
            )

    # Module-vs-directory collision pre-flight along the path.
    cur = root
    for seg in parts:
        py = cur / f"{seg}.py"
        sub = cur / seg
        if py.is_file() and sub.is_dir():
            raise ConfigLoadError(
                f"name collision under {cur}: both '{py.name}' and '{sub.name}/' "
                f"exist. Python can import only one as '{seg}' — remove or rename "
                f"one of them."
            )
        cur = sub

    return ".".join([PLAN_PACKAGE, *parts])


def import_plan_module(dotted: str, *, path_hint: Path | None = None) -> ModuleType:
    """Import a ``clarinet_plan.*`` submodule, classifying failures.

    Returns the imported module.  Raises ``ConfigLoadError`` with a precise
    message:

    * the plan module itself is missing (``ModuleNotFoundError`` naming *dotted*
      or a prefix of it) → "plan module not found";
    * a module/directory name collision past the pre-flight (``... is not a
      package``) → dedicated message;
    * a transitive sibling/third-party dependency is missing → ordinary
      import-failure wrapper, plus a migration hint when the bare (unprefixed)
      name still resolves to a file/dir under the root;
    * any other exec error → ordinary import-failure wrapper.

    A half-initialized module is removed from ``sys.modules`` by Python itself
    on failure; surviving transitively-imported siblings are cleared by the next
    activate/deactivate purge, so no manual pop is needed here.
    """
    try:
        return importlib.import_module(dotted)
    except ModuleNotFoundError as e:
        text = str(e)
        missing = e.name or ""
        if "is not a package" in text:
            raise ConfigLoadError(
                f"failed to import {dotted}: {text}. A module and a directory "
                f"likely share a name under the plan root — rename one of them.",
                path=path_hint,
            ) from e
        is_plan_missing = missing == dotted or dotted.startswith(missing + ".")
        if is_plan_missing:
            raise ConfigLoadError(
                f"plan module '{dotted}' not found"
                + (f" (expected file: {path_hint})" if path_hint else "")
                + ".",
                path=path_hint,
            ) from e
        # The file exec'd but an import inside it failed — a missing third-party
        # dependency, or a pre-migration sibling import. Wrap, hint if relevant.
        hint = _migration_hint(missing)
        message = f"failed to import {dotted}: {e!r}"
        if hint:
            message += "\n" + hint
        raise ConfigLoadError(message, path=path_hint) from e
    except ImportError as e:
        raise ConfigLoadError(f"failed to import {dotted}: {e!r}", path=path_hint) from e
    except Exception as e:
        raise ConfigLoadError(f"failed to import {dotted}: {e!r}", path=path_hint) from e


def _migration_hint(missing: str) -> str | None:
    """Build a migration hint when *missing* names a real plan file/dir.

    If a transitive import failed on a bare (un-prefixed) name that still exists
    under the plan root, the cause is almost certainly an un-migrated import —
    suggest the ``clarinet_plan.``-prefixed spelling so the breaking change
    becomes a self-explaining error.  Returns ``None`` for genuine third-party
    misses (no matching file/dir under the root).
    """
    if not missing or missing == PLAN_PACKAGE or missing.startswith(PLAN_PACKAGE + "."):
        return None
    root = plan_root()
    if root is None:
        return None
    segs = missing.split(".")
    as_py = root.joinpath(*segs).with_suffix(".py")
    as_dir = root.joinpath(*segs)
    if not (as_py.is_file() or as_dir.is_dir()):
        return None
    canonical = f"{PLAN_PACKAGE}.{missing}"
    return (
        f"hint: plan imports now require the '{PLAN_PACKAGE}.' prefix — replace "
        f"'from {missing} import ...' with 'from {canonical} import ...' "
        f"(relative imports like 'from .{segs[-1]} import ...' also work inside "
        f"the same subpackage)."
    )
