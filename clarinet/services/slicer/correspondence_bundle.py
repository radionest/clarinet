"""Flatten the correspondence engine (+ grid vocabulary) into one injectable blob.

3D Slicer runs its own bundled Python without ``clarinet`` installed. This
module concatenates the source of the correspondence package's modules plus
the standalone ``grid`` module (in dependency order) into a single string
with all ``clarinet``-internal imports stripped, so the resulting text can be
prepended to a Slicer script and ``exec()``'d there to make
``build_overlap_graph`` and ``grid_relation`` callable.
"""

import inspect

from clarinet.services.image import grid
from clarinet.services.image.correspondence import graph, matching, measures, model, operations

# Dependency order: model first (the engine); grid is standalone (numpy + stdlib
# only, no clarinet imports) and rides along for the export-time grid vocabulary
# (Grid, grid_relation, RelationKind) that helper.py's export_segmentation uses.
_MODULES = (model, measures, matching, operations, graph, grid)
_cache: str | None = None

_FUTURE_LINE = "from __future__ import annotations\n"
_SELF_REGISTER_LINES = (
    "import sys as _bundle_sys, types as _bundle_types\n"
    "_bundle_sys.modules.setdefault(__name__, _bundle_types.ModuleType(__name__))\n"
)


def _prelude(standalone: bool) -> str:
    """Prelude prepended to the flattened body; see :func:`build_correspondence_bundle`."""
    return (_FUTURE_LINE if standalone else "") + _SELF_REGISTER_LINES


def build_correspondence_bundle(*, standalone: bool = True) -> str:
    """Return the correspondence engine + grid module source, flattened and import-free.

    Concatenates ``inspect.getsource()`` of each module in ``_MODULES``,
    dropping every ``from clarinet ...`` / ``import clarinet ...`` line
    (including multi-line parenthesized ones -- a naive single-line strip
    would leave dangling ``    Name,`` continuation lines and raise
    ``SyntaxError``) and every per-module ``from __future__ import ...`` line,
    then prepending a two-line prelude.

    The prelude is load-bearing for exec'ing this text as a *synthetic* module:

    1. ``from __future__ import annotations`` -- Slicer's bundled Python (3.9)
       and CI (3.12) evaluate annotations eagerly, so a self-referential
       annotation such as ``grid``'s ``Grid.from_components(...) -> Grid`` (and
       any ``X | None`` union under 3.9) would raise at class-definition time
       without it. It must be the first line (a future import cannot follow
       other statements).
    2. A ``sys.modules.setdefault(__name__, ...)`` self-registration -- with (1)
       active, annotations are strings, and ``@dataclass`` resolves them via
       ``sys.modules[cls.__module__].__dict__`` while detecting ClassVar/InitVar.
       When this text is exec'd under a ``__name__`` that is not a real module
       (e.g. the tests' ``"_bundle"``), that lookup would raise ``AttributeError``
       on ``None``; registering an empty stand-in module makes the lookup resolve
       (to "not a ClassVar", correct here) instead of crashing. A no-op when
       ``__name__`` is already a real module (e.g. Slicer's ``__main__``).

    ``standalone=False`` omits (1) only, for callers that concatenate this text
    *after* other source which already opens the compiled unit with a future
    import -- ``SlicerService._build_script`` composes ``helper.py + bundle +
    runner``, and a second future import mid-unit is a ``SyntaxError`` ("must
    occur at the beginning of the file"), so the composed Slicer script would
    not compile at all. Part (2) is emitted either way: it is a no-op under a
    real ``__name__`` and still needed when the composed text is exec'd under a
    synthetic one.

    The flattened body is cached after the first call since the source text
    never changes at runtime; the prelude is cheap and composed per call.
    """
    global _cache
    if _cache is not None:
        return _prelude(standalone) + _cache
    out: list[str] = []
    for mod in _MODULES:
        skip_paren = False
        for line in inspect.getsource(mod).splitlines():
            s = line.strip()
            if skip_paren:  # inside a stripped ( ... ) import
                if ")" in s:
                    skip_paren = False
                continue
            if s.startswith("from clarinet") or s.startswith("import clarinet"):
                if "(" in s and ")" not in s:
                    skip_paren = True
                continue
            if s.startswith("from __future__ import"):
                continue
            out.append(line)
        out.append("")
    _cache = "\n".join(out)
    return _prelude(standalone) + _cache
