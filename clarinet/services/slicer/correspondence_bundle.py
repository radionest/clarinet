"""Flatten the correspondence engine into one injectable source blob.

3D Slicer runs its own bundled Python without ``clarinet`` installed. This
module concatenates the source of the correspondence package's modules (in
dependency order) into a single string with all ``clarinet``-internal
imports stripped, so the resulting text can be prepended to a Slicer script
and ``exec()``'d there to make ``build_overlap_graph`` callable.
"""

import inspect

from clarinet.services.image.correspondence import graph, matching, measures, model, operations

_MODULES = (model, measures, matching, operations, graph)  # dependency order: model first
_cache: str | None = None


def build_correspondence_bundle() -> str:
    """Return the correspondence engine's source, flattened and import-free.

    Concatenates ``inspect.getsource()`` of each module in ``_MODULES``,
    dropping every ``from clarinet.services.image.correspondence ...`` import
    (including multi-line parenthesized ones -- a naive single-line strip
    would leave dangling ``    Name,`` continuation lines and raise
    ``SyntaxError``) and every ``from __future__ import ...`` line. Result is
    cached after the first call since the source text never changes at
    runtime.
    """
    global _cache
    if _cache is not None:
        return _cache
    out: list[str] = []
    for mod in _MODULES:
        skip_paren = False
        for line in inspect.getsource(mod).splitlines():
            s = line.strip()
            if skip_paren:  # inside a stripped ( ... ) import
                if ")" in s:
                    skip_paren = False
                continue
            if s.startswith("from clarinet.services.image.correspondence"):
                if "(" in s and ")" not in s:
                    skip_paren = True
                continue
            if s.startswith("from __future__ import"):
                continue
            out.append(line)
        out.append("")
    _cache = "\n".join(out)
    return _cache
