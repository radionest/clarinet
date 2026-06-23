"""Bundle external JSON-Schema ``$ref`` targets into a schema's local ``$defs``.

Downstream projects keep shared definitions in a separate ``.json`` file and
reference them with a standard relative ``$ref``
(``"common.schema.json#/$defs/Name"``). At config-load time each such target is
inlined into the *referencing* schema's own ``$defs`` and the ref rewritten to
``"#/$defs/Name"``, producing a self-contained schema. Every downstream consumer
(the ``jsonschema`` validator, schema hydration, the formosh frontend) already
resolves intra-document ``#/$defs/...`` — so none of them change.

Scope (intentional): only ``<file>#/$defs/<Name>`` (and ``#/definitions/<Name>``)
refs, single layer, plus any sibling ``#/$defs/*`` the pulled def needs inside the
same file. A whole-file ref, a ref chain across files, or an external ref nested
in a pulled def all raise :class:`ConfigLoadError`.
"""

import copy
import json
from pathlib import Path
from typing import Any, cast

from clarinet.exceptions.domain import ConfigLoadError


def bundle_external_defs(schema: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    """Inline external ``$ref`` targets into ``schema``'s local ``$defs``.

    Args:
        schema: Parsed JSON-Schema document. Not mutated.
        base_dir: Directory that relative ``$ref`` file parts resolve against
            (the referencing schema file's own directory).

    Returns:
        A new self-contained schema: every external ``$ref`` rewritten to
        ``#/$defs/<Name>`` and its target (plus any sibling ``#/$defs/*`` used
        inside the same file) copied into the root ``$defs``.

    Raises:
        ConfigLoadError: missing/unparsable external file; pointer not into
            ``$defs``/``definitions``; whole-file ref; cross-file chain; or a
            name collision (``$defs`` key already present with different content).
    """
    result = copy.deepcopy(schema)
    hoisted: dict[str, Any] = {}
    source: dict[str, str] = {}
    file_cache: dict[Path, dict[str, Any]] = {}

    def _load_file(file_part: str) -> dict[str, Any]:
        path = (base_dir / file_part).resolve()
        if path not in file_cache:
            try:
                file_cache[path] = json.loads(path.read_text(encoding="utf-8"))
            except OSError as e:
                raise ConfigLoadError(
                    f"Shared schema file for $ref '{file_part}' not found: {path}",
                    path=path,
                ) from e
            except json.JSONDecodeError as e:
                raise ConfigLoadError(
                    f"Shared schema file '{file_part}' is not valid JSON: {e}",
                    path=path,
                ) from e
        return file_cache[path]

    def _def_name(pointer: str) -> str:
        # pointer is the part after '#', e.g. "/$defs/Name" or "/definitions/Name"
        parts = pointer.split("/")
        if len(parts) == 3 and parts[0] == "" and parts[1] in ("$defs", "definitions"):
            return parts[2]
        raise ConfigLoadError(
            f"Unsupported $ref pointer '#{pointer}' — only '#/$defs/<Name>' "
            f"(or '#/definitions/<Name>') cross-file refs are supported"
        )

    def _normalize(node: Any, file_part: str) -> Any:
        # Inside a hoisted def: pull sibling intra-file refs, forbid cross-file refs.
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    if v.startswith("#/$defs/") or v.startswith("#/definitions/"):
                        sib = v.split("/")[-1]
                        _register(sib, file_part)
                        out[k] = f"#/$defs/{sib}"
                    elif v.startswith("#"):
                        # A portable hoisted def may only self-reference sibling
                        # $defs entries (handled above). Any other intra-doc
                        # pointer (e.g. #/properties/x) would silently re-resolve
                        # against the destination document once the def is hoisted,
                        # so reject it — keep the fail-fast single-layer invariant.
                        raise ConfigLoadError(
                            f"Unsupported intra-document $ref inside a shared def: "
                            f"'{v}' — only sibling '#/$defs/<Name>' refs are allowed"
                        )
                    else:
                        raise ConfigLoadError(
                            f"Cross-file $ref chain not supported: a shared def references '{v}'"
                        )
                else:
                    out[k] = _normalize(v, file_part)
            return out
        if isinstance(node, list):
            return [_normalize(x, file_part) for x in node]
        return node

    def _register(name: str, file_part: str) -> None:
        if name in hoisted:
            prior = source.get(name)
            if prior is not None and prior != file_part:
                raise ConfigLoadError(
                    f"$defs name '{name}' is referenced from two different "
                    f"files ('{prior}' and '{file_part}') — rename one so the "
                    f"local $defs key is unambiguous"
                )
            return
        source[name] = file_part
        defs = _load_file(file_part)
        pool = defs.get("$defs") or defs.get("definitions") or {}
        if name not in pool:
            raise ConfigLoadError(
                f"$ref '{file_part}#/$defs/{name}' — definition '{name}' not found in {file_part}"
            )
        hoisted[name] = None  # reserve before recursing → breaks self-reference cycles
        hoisted[name] = _normalize(copy.deepcopy(pool[name]), file_part)

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                file_part, sep, pointer = ref.partition("#")
                if not sep or not pointer:
                    raise ConfigLoadError(
                        f"Whole-file $ref not supported: '{ref}' — use '<file>#/$defs/<Name>'"
                    )
                name = _def_name(pointer)
                _register(name, file_part)
                rewritten = {k: _walk(v) for k, v in node.items() if k != "$ref"}
                rewritten["$ref"] = f"#/$defs/{name}"
                return rewritten
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(x) for x in node]
        return node

    result = cast(dict[str, Any], _walk(result))

    if hoisted:
        existing = result.setdefault("$defs", {})
        for name, value in hoisted.items():
            if name in existing and existing[name] != value:
                raise ConfigLoadError(
                    f"$defs name collision on '{name}': the schema already "
                    f"defines it with different content"
                )
            existing[name] = value
    return result
