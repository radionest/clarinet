"""Render the OHIF app-config.js dataSources block from settings.

The OHIF data source OHIF talks to is selected per project via
``settings.dicomweb_backend`` (see clarinet/settings.py). The package ships
``clarinet/ohif/app-config.js`` with a sentinel where the ``dataSources``
array goes; ``serve_spa`` (clarinet/api/app.py) injects the rendered block at
request time so changing the backend is a settings edit + restart, no reinstall.
"""

from __future__ import annotations

import json
from typing import Any

DATASOURCES_SENTINEL = "__CLARINET_DATASOURCES__"


def _flag(value: bool | None, *, backend: str) -> bool:
    """Resolve an OHIF capability flag: explicit value, else per-backend default."""
    if value is not None:
        return value
    return backend == "external"  # external -> True (fast), builtin -> False


def build_datasources(
    *,
    backend: str,
    external_root: str | None,
    friendly_name: str,
    qido_include: bool | None,
    fuzzy: bool | None,
    wildcard: bool | None,
    base_path: str,
) -> list[dict[str, Any]]:
    """Build the OHIF dataSources list for the selected backend."""
    base = base_path.rstrip("/")
    if backend == "external":
        if not external_root:
            msg = "external backend requires a non-empty external_root"
            raise ValueError(msg)
        root = f"{base}{external_root}"
    else:
        root = f"{base}/dicom-web"
    return [
        {
            "namespace": "@ohif/extension-default.dataSourcesModule.dicomweb",
            "sourceName": "dicomweb",
            "configuration": {
                "friendlyName": friendly_name,
                "name": "clarinet",
                "wadoUriRoot": root,
                "qidoRoot": root,
                "wadoRoot": root,
                "qidoSupportsIncludeField": _flag(qido_include, backend=backend),
                "imageRendering": "wadors",
                "thumbnailRendering": "wadors",
                "supportsFuzzyMatching": _flag(fuzzy, backend=backend),
                "supportsWildcard": _flag(wildcard, backend=backend),
            },
        }
    ]


def render_datasources_js(
    *,
    backend: str,
    external_root: str | None,
    friendly_name: str,
    qido_include: bool | None,
    fuzzy: bool | None,
    wildcard: bool | None,
    base_path: str,
) -> str:
    """Return the dataSources list as a JSON (== valid JS) literal string."""
    return json.dumps(
        build_datasources(
            backend=backend,
            external_root=external_root,
            friendly_name=friendly_name,
            qido_include=qido_include,
            fuzzy=fuzzy,
            wildcard=wildcard,
            base_path=base_path,
        ),
        indent=2,
    )


def inject_datasources(app_config_text: str, datasources_js: str) -> str | None:
    """Replace the sentinel with the rendered block; None if the sentinel is absent."""
    if DATASOURCES_SENTINEL not in app_config_text:
        return None
    return app_config_text.replace(DATASOURCES_SENTINEL, datasources_js)
