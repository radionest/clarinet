# Per-project DICOMweb backend (builtin/external Orthanc) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Clarinet project choose the DICOMweb backend OHIF talks to (`builtin` in-process proxy, or `external` Orthanc reverse-proxied same-origin by nginx) and render OHIF's `app-config.js` dataSources from settings at serve time.

**Architecture:** Settings select the backend + carry its params. A pure helper (`clarinet/api/ohif_config.py`) builds the OHIF `dataSources` JS; `serve_spa` injects it into `ohif/app-config.js` at request time (cached). `/api/info` exposes the backend so the frontend gates the (builtin-only) preload widget. External auth is nginx `auth_request` → existing `/api/auth/session/validate` (docs-only reference config in `docs/`).

**Tech Stack:** Python 3 / FastAPI / pydantic-settings; Gleam + Lustre frontend (gleeunit); Orthanc + Lua + nginx (reference docs only).

**Spec:** `docs/superpowers/specs/2026-06-22-orthanc-dicomweb-proxy-design.md`

## Global Constraints

- **Default `dicomweb_backend = "builtin"`** → existing projects render today's dataSources, zero behavior change.
- **No alembic migration** (settings-only; Clarinet is a framework — migrations are project-level).
- **External topology is same-origin only** (relative root, base_path-prefixed); absolute cross-origin URLs are out of scope.
- **Per-backend OHIF flag defaults:** unset (`None`) → `builtin`: `False`, `external`: `True` (`qidoSupportsIncludeField`, `supportsFuzzyMatching`, `supportsWildcard`).
- Python tools via `uv run …`; lint/format/type with `make check`. Tests redirected to `/tmp/test-dicomweb-backend.txt 2>&1` (never piped).
- Commits: Conventional Commits, English, **no** `Co-Authored-By`.
- Frontend gleam runs from `clarinet/frontend/`; verify with `make frontend-check` / `make frontend-build`.

---

### Task 1: Settings — backend selection + fail-fast validation

**Files:**
- Modify: `clarinet/settings.py:16` (pydantic import), after `:247` (new fields), and add a `@model_validator` method in the `Settings` class (near the existing `@field_validator`s, ~`:136-175`).
- Test: `tests/test_dicomweb_settings.py` (create)

**Interfaces:**
- Produces: `settings.dicomweb_backend: Literal["builtin","external"]`, `settings.dicomweb_external_root: str | None`, `settings.dicomweb_friendly_name: str`, `settings.dicomweb_qido_supports_include_field|_supports_fuzzy_matching|_supports_wildcard: bool | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dicomweb_settings.py
import pytest
from pydantic import ValidationError

from clarinet.settings import Settings


def test_external_backend_requires_root():
    with pytest.raises(ValidationError, match="dicomweb_external_root"):
        Settings(dicomweb_backend="external")


def test_external_backend_with_root_ok():
    s = Settings(dicomweb_backend="external", dicomweb_external_root="/pacs-web")
    assert s.dicomweb_external_root == "/pacs-web"


def test_default_backend_is_builtin():
    assert Settings().dicomweb_backend == "builtin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dicomweb_settings.py -v > /tmp/test-dicomweb-backend.txt 2>&1`
Expected: FAIL — `Settings(dicomweb_backend="external")` does not raise (field/validator absent).

- [ ] **Step 3: Write minimal implementation**

In `clarinet/settings.py`, change the pydantic import (`:16`):

```python
from pydantic import SecretStr, field_validator, model_validator
```

Add the fields immediately after `dicomweb_dcm_anon_path_cache_ttl_seconds` (`:247`):

```python
    # --- DICOMweb backend selection (which proxy OHIF talks to) ---
    # "builtin" = in-process proxy router (/dicom-web); "external" = a separate
    # DICOMweb server (e.g. Orthanc) reverse-proxied same-origin by nginx.
    # Drives the OHIF app-config.js dataSources block (clarinet/api/ohif_config.py).
    dicomweb_backend: Literal["builtin", "external"] = "builtin"
    # Same-origin path OHIF uses for QIDO/WADO when backend == "external"
    # (e.g. "/pacs-web"); nginx proxies it to the local DICOMweb server. The
    # deploy base path is prepended at render time. Required when "external".
    dicomweb_external_root: str | None = None
    dicomweb_friendly_name: str = "Clarinet PACS"
    # OHIF capability flags. None -> per-backend default (builtin: False,
    # external: True), resolved in clarinet/api/ohif_config.py.
    dicomweb_qido_supports_include_field: bool | None = None
    dicomweb_supports_fuzzy_matching: bool | None = None
    dicomweb_supports_wildcard: bool | None = None
```

Add the validator method inside the `Settings` class (alongside the other validators):

```python
    @model_validator(mode="after")
    def _check_dicomweb_external_root(self) -> Self:
        if self.dicomweb_backend == "external" and not self.dicomweb_external_root:
            msg = (
                "dicomweb_backend='external' requires dicomweb_external_root "
                "(e.g. '/pacs-web')"
            )
            raise ValueError(msg)
        return self
```

(`Literal` and `Self` are already imported at `:14`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dicomweb_settings.py -v > /tmp/test-dicomweb-backend.txt 2>&1`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add clarinet/settings.py tests/test_dicomweb_settings.py
git commit -m "feat(dicomweb): add backend selection settings with fail-fast validation"
```

---

### Task 2: OHIF dataSources renderer (pure)

**Files:**
- Create: `clarinet/api/ohif_config.py`
- Test: `tests/test_ohif_app_config.py` (create)

**Interfaces:**
- Produces:
  - `DATASOURCES_SENTINEL: str = "__CLARINET_DATASOURCES__"`
  - `build_datasources(*, backend, external_root, friendly_name, qido_include, fuzzy, wildcard, base_path) -> list[dict]`
  - `render_datasources_js(*, backend, external_root, friendly_name, qido_include, fuzzy, wildcard, base_path) -> str`
  - `inject_datasources(app_config_text: str, datasources_js: str) -> str | None` (None when the sentinel is absent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ohif_app_config.py
from clarinet.api.ohif_config import (
    DATASOURCES_SENTINEL,
    build_datasources,
    inject_datasources,
)


def _cfg(backend, external_root):
    return build_datasources(
        backend=backend,
        external_root=external_root,
        friendly_name="Clarinet PACS",
        qido_include=None,
        fuzzy=None,
        wildcard=None,
        base_path="/liver_nir",
    )[0]["configuration"]


def test_builtin_roots_and_conservative_flags():
    cfg = _cfg("builtin", None)
    assert cfg["qidoRoot"] == "/liver_nir/dicom-web"
    assert cfg["wadoRoot"] == "/liver_nir/dicom-web"
    assert cfg["wadoUriRoot"] == "/liver_nir/dicom-web"
    assert cfg["qidoSupportsIncludeField"] is False
    assert cfg["supportsFuzzyMatching"] is False
    assert cfg["supportsWildcard"] is False


def test_external_roots_and_fast_flags():
    cfg = _cfg("external", "/pacs-web")
    assert cfg["qidoRoot"] == "/liver_nir/pacs-web"
    assert cfg["qidoSupportsIncludeField"] is True
    assert cfg["supportsFuzzyMatching"] is True
    assert cfg["supportsWildcard"] is True


def test_inject_replaces_sentinel():
    text = f"window.config = {{ dataSources: {DATASOURCES_SENTINEL}, x: 1 }};"
    out = inject_datasources(text, "[1,2,3]")
    assert out is not None
    assert DATASOURCES_SENTINEL not in out
    assert "[1,2,3]" in out


def test_inject_absent_sentinel_returns_none():
    assert inject_datasources("no sentinel", "[]") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ohif_app_config.py -v > /tmp/test-dicomweb-backend.txt 2>&1`
Expected: FAIL — `ModuleNotFoundError: clarinet.api.ohif_config`.

- [ ] **Step 3: Write minimal implementation**

```python
# clarinet/api/ohif_config.py
"""Render the OHIF app-config.js dataSources block from settings.

The OHIF data source OHIF talks to is selected per project via
``settings.dicomweb_backend`` (see clarinet/settings.py). The package ships
``clarinet/ohif/app-config.js`` with a sentinel where the ``dataSources``
array goes; ``serve_spa`` (clarinet/api/app.py) injects the rendered block at
request time so changing the backend is a settings edit + restart, no reinstall.
"""

from __future__ import annotations

import json

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
) -> list[dict]:
    """Build the OHIF dataSources list for the selected backend."""
    base = base_path.rstrip("/")
    if backend == "external":
        # external_root is validated non-None upstream (Settings validator).
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


def render_datasources_js(**kwargs) -> str:
    """Return the dataSources list as a JSON (== valid JS) literal string."""
    return json.dumps(build_datasources(**kwargs), indent=2)


def inject_datasources(app_config_text: str, datasources_js: str) -> str | None:
    """Replace the sentinel with the rendered block; None if the sentinel is absent."""
    if DATASOURCES_SENTINEL not in app_config_text:
        return None
    return app_config_text.replace(DATASOURCES_SENTINEL, datasources_js)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ohif_app_config.py -v > /tmp/test-dicomweb-backend.txt 2>&1`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add clarinet/api/ohif_config.py tests/test_ohif_app_config.py
git commit -m "feat(dicomweb): pure OHIF dataSources renderer (builtin/external)"
```

---

### Task 3: Serve rendered app-config.js + template sentinel + installer cleanup

**Files:**
- Modify: `clarinet/ohif/app-config.js:33-51` (replace the `dataSources` array literal with the sentinel)
- Modify: `clarinet/api/app.py` — import `Response` + `ohif_config`; add `_app_config_cache`; intercept `ohif/app-config.js` in `serve_spa` (`:652`)
- Modify: `clarinet/cli/main.py:722-723` (drop the dead `'/dicom-web'` replacement)
- Test: `tests/test_ohif_app_config.py` (append serve test)

**Interfaces:**
- Consumes: `ohif_config.render_datasources_js`, `ohif_config.inject_datasources`, `ohif_config.DATASOURCES_SENTINEL` (Task 2); `settings.dicomweb_*` (Task 1).

- [ ] **Step 1: Write the failing test** (append to `tests/test_ohif_app_config.py`)

```python
def test_serve_app_config_renders_external(tmp_path, monkeypatch):
    # NOTE: do NOT use `with TestClient(...)` — that runs the app lifespan
    # (DB init etc.). serve_spa only needs settings + files, no lifespan.
    from fastapi.testclient import TestClient

    from clarinet.api.app import create_app
    from clarinet.settings import settings

    ohif = tmp_path / "ohif"
    ohif.mkdir()
    (ohif / "app-config.js").write_text(
        "window.config = { dataSources: __CLARINET_DATASOURCES__, "
        "defaultDataSourceName: 'dicomweb' };",
        encoding="utf-8",
    )
    monkeypatch.setattr(type(settings), "ohif_path", property(lambda _self: ohif))
    monkeypatch.setattr(settings, "ohif_enabled", True)
    monkeypatch.setattr(settings, "dicomweb_backend", "external")
    monkeypatch.setattr(settings, "dicomweb_external_root", "/pacs-web")

    client = TestClient(create_app(root_path=""))
    resp = client.get("/ohif/app-config.js")
    assert resp.status_code == 200
    assert "/pacs-web" in resp.text
    assert "__CLARINET_DATASOURCES__" not in resp.text
    assert resp.headers["content-type"].startswith("application/javascript")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ohif_app_config.py::test_serve_app_config_renders_external -v > /tmp/test-dicomweb-backend.txt 2>&1`
Expected: FAIL — sentinel still present / wrong content-type (no interception yet).

- [ ] **Step 3: Write minimal implementation**

In `clarinet/ohif/app-config.js`, replace the `dataSources: [ … ]` array (`:33-50`) and keep `defaultDataSourceName`:

```js
  dataSources: __CLARINET_DATASOURCES__,
  defaultDataSourceName: 'dicomweb',
```

In `clarinet/api/app.py`, add to the response imports (near `FileResponse`/`HTMLResponse`):

```python
from fastapi.responses import Response
```

and import the helper at top of the module:

```python
from clarinet.api import ohif_config
```

Inside `create_app`, next to `_index_html_cache` (`:628`):

```python
        _app_config_cache: dict[str, str] = {}
```

In `serve_spa`, at the very start of the `if full_path.startswith("ohif/"):` block (`:652`), before the static-file attempt:

```python
            if full_path == "ohif/app-config.js" and settings.ohif_enabled:
                cfg_path = ohif_dir / "app-config.js"
                if cfg_path.is_file():
                    if root_path not in _app_config_cache:
                        text = cfg_path.read_text(encoding="utf-8")
                        js = ohif_config.render_datasources_js(
                            backend=settings.dicomweb_backend,
                            external_root=settings.dicomweb_external_root,
                            friendly_name=settings.dicomweb_friendly_name,
                            qido_include=settings.dicomweb_qido_supports_include_field,
                            fuzzy=settings.dicomweb_supports_fuzzy_matching,
                            wildcard=settings.dicomweb_supports_wildcard,
                            base_path=root_path,
                        )
                        rendered = ohif_config.inject_datasources(text, js)
                        if rendered is None:
                            logger.warning(
                                f"OHIF app-config.js missing {ohif_config.DATASOURCES_SENTINEL} "
                                "sentinel; serving unrendered. Run "
                                "'clarinet ohif install --force-config' to refresh."
                            )
                            rendered = text
                        _app_config_cache[root_path] = rendered
                    return Response(
                        _app_config_cache[root_path],
                        media_type="application/javascript",
                    )
```

In `clarinet/cli/main.py`, remove the now-dead lines (`:722-723`) inside `_patch_ohif_paths`:

```python
        dicomweb_path = f"{base_path}/dicom-web"
        config = config.replace("'/dicom-web'", f"'{dicomweb_path}'")
```

(Keep the `routerBasename` replacement directly above it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ohif_app_config.py -v > /tmp/test-dicomweb-backend.txt 2>&1`
Expected: PASS (all, including the serve test).

- [ ] **Step 5: Commit**

```bash
git add clarinet/ohif/app-config.js clarinet/api/app.py clarinet/cli/main.py tests/test_ohif_app_config.py
git commit -m "feat(dicomweb): render OHIF app-config dataSources from settings at serve time"
```

---

### Task 4: Expose backend in /api/info

**Files:**
- Modify: `clarinet/api/routers/info.py:18-24`
- Test: `tests/test_info_endpoint.py` (create)

**Interfaces:**
- Produces: `/api/info` JSON key `"dicomweb_backend"` (string).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_info_endpoint.py
import pytest

from clarinet.api.routers.info import get_project_info
from clarinet.services.viewer import ViewerRegistry


@pytest.mark.asyncio
async def test_info_includes_dicomweb_backend():
    info = await get_project_info(registry=ViewerRegistry())
    assert info["dicomweb_backend"] == "builtin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_info_endpoint.py -v > /tmp/test-dicomweb-backend.txt 2>&1`
Expected: FAIL — `KeyError: 'dicomweb_backend'`.

- [ ] **Step 3: Write minimal implementation**

In `clarinet/api/routers/info.py`, add the key to the returned dict:

```python
    return {
        "project_name": settings.project_name,
        "project_description": settings.project_description,
        "viewers": registry.viewer_info(),
        "sse_enabled": settings.sse_enabled,
        "anon_per_study_patient_id": settings.anon_per_study_patient_id,
        "dicomweb_backend": settings.dicomweb_backend,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_info_endpoint.py -v > /tmp/test-dicomweb-backend.txt 2>&1`
Expected: PASS. (If the project's pytest is not in `asyncio_mode=auto`, the `@pytest.mark.asyncio` marker covers it.)

- [ ] **Step 5: Commit**

```bash
git add clarinet/api/routers/info.py tests/test_info_endpoint.py
git commit -m "feat(dicomweb): expose dicomweb_backend via /api/info"
```

---

### Task 5: Frontend — decode backend + gate preload widget

**Files:**
- Modify: `clarinet/frontend/src/api/info.gleam` (ProjectInfo type + decoder)
- Modify: `clarinet/frontend/src/store.gleam` (Model field + default ~`:211`)
- Modify: `clarinet/frontend/src/main.gleam` (`ProjectInfoLoaded(Ok)` ~`:478`, `build_shared` ~`:942`)
- Modify: `clarinet/frontend/src/shared.gleam` (Shared field)
- Modify: `clarinet/frontend/src/utils/viewer.gleam` (`ohif_preload_enabled` helper, `record_viewer_buttons` + `ohif_record_button` gating)
- Modify: `clarinet/frontend/src/pages/records/execute.gleam:1436` (pass the flag)
- Test: `clarinet/frontend/test/viewer_test.gleam` (create)

**Interfaces:**
- Consumes: `/api/info` `dicomweb_backend` (Task 4).
- Produces: `viewer.ohif_preload_enabled(backend: String) -> Bool`; `shared.Shared.dicomweb_backend: String`.

- [ ] **Step 1: Write the failing test**

```gleam
// clarinet/frontend/test/viewer_test.gleam
import gleeunit/should
import utils/viewer

pub fn preload_enabled_for_builtin_test() {
  viewer.ohif_preload_enabled("builtin") |> should.be_true
}

pub fn preload_disabled_for_external_test() {
  viewer.ohif_preload_enabled("external") |> should.be_false
}
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `clarinet/frontend/`): `gleam test > /tmp/test-dicomweb-frontend.txt 2>&1`
Expected: FAIL — `ohif_preload_enabled` is unknown.

- [ ] **Step 3: Write minimal implementation**

`api/info.gleam` — add a field to `ProjectInfo` and the decoder:

```gleam
pub type ProjectInfo {
  ProjectInfo(
    project_name: String,
    project_description: String,
    viewers: List(ViewerInfo),
    sse_enabled: Bool,
    anon_per_study: Bool,
    dicomweb_backend: String,
  )
}
```

```gleam
  use anon_per_study <- decode.optional_field(
    "anon_per_study_patient_id",
    False,
    decode.bool,
  )
  use dicomweb_backend <- decode.optional_field(
    "dicomweb_backend",
    "builtin",
    decode.string,
  )
  decode.success(ProjectInfo(
    project_name:,
    project_description:,
    viewers:,
    sse_enabled:,
    anon_per_study:,
    dicomweb_backend:,
  ))
```

`store.gleam` — add `dicomweb_backend: String` to `Model` (next to `anon_per_study`), default in the initial model (`:211`): `dicomweb_backend: "builtin",`. If `store` copies fields into a sub-record at `:236`, add `dicomweb_backend: model.dicomweb_backend,` there too (match the existing `anon_per_study` lines).

`main.gleam` — in `store.ProjectInfoLoaded(Ok(project_info))` (`:478`) add:

```gleam
          anon_per_study: project_info.anon_per_study,
          dicomweb_backend: project_info.dicomweb_backend,
```

and in `build_shared` (`:942`) add:

```gleam
    anon_per_study: model.anon_per_study,
    dicomweb_backend: model.dicomweb_backend,
```

`shared.gleam` — add to `Shared` (next to `anon_per_study`, `:28`):

```gleam
    // Selected DICOMweb backend ("builtin" | "external") — gates the
    // builtin-only OHIF preload widget.
    dicomweb_backend: String,
```

`utils/viewer.gleam` — add the predicate and thread it through:

```gleam
/// OHIF preload warms the builtin DICOMweb cache; it is meaningless for an
/// external backend (Orthanc), so the OHIF button opens as a plain link there.
pub fn ohif_preload_enabled(dicomweb_backend: String) -> Bool {
  dicomweb_backend == "builtin"
}
```

Add a `preload_enabled: Bool` parameter to `record_viewer_buttons` (after `viewer_mode`), and pass it into both `ohif_record_button(...)` calls. In `ohif_record_button`, add the same `preload_enabled: Bool` parameter and, at both `html.button(...)` return points, branch:

```gleam
      case preload_enabled {
        True ->
          html.button(
            [attribute.class(class), event.on_click(on_view(url, study_uids))],
            [html.text("OHIF")],
          )
        False -> viewer_link(url, Ohif, class)
      }
```

(Apply the same `case preload_enabled` wrap to the `Some(uid_list)` branch button, using its `url`/`uid_list`.)

`pages/records/execute.gleam:1436` — pass the flag at the single call site:

```gleam
      viewer.record_viewer_buttons(
        // …existing args…,
        viewer.ohif_preload_enabled(shared.dicomweb_backend),
        // …on_view callback…,
      )
```

- [ ] **Step 4: Run test + type-check + build**

Run (from `clarinet/frontend/`):
```bash
gleam test > /tmp/test-dicomweb-frontend.txt 2>&1
```
Expected: PASS.

Then from repo root:
```bash
make frontend-check > /tmp/test-dicomweb-frontend-check.txt 2>&1
make frontend-build > /tmp/test-dicomweb-frontend-build.txt 2>&1
```
Expected: type-check clean; bundle built.

- [ ] **Step 5: Commit**

```bash
git add clarinet/frontend/src/api/info.gleam clarinet/frontend/src/store.gleam \
  clarinet/frontend/src/main.gleam clarinet/frontend/src/shared.gleam \
  clarinet/frontend/src/utils/viewer.gleam \
  clarinet/frontend/src/pages/records/execute.gleam \
  clarinet/frontend/test/viewer_test.gleam clarinet/static/clarinet_frontend.js
git commit -m "feat(dicomweb): gate OHIF preload widget by backend in frontend"
```

---

### Task 6: Reference config doc (Orthanc + Lua + nginx)

**Files:**
- Create: `docs/orthanc-dicomweb-proxy.md`

No automated test — documentation only. Content must be concrete enough to reproduce the deploy, but is **not** wired into the installer or CI (per spec §5).

- [ ] **Step 1: Write the reference doc**

Sections (each with a copy-pasteable block):

1. **Overview** — external backend = Orthanc on `127.0.0.1`, nginx reverse-proxies `{base_path}/pacs-web/` to it; caching proxy that serves first, then persists (mirrors the builtin proxy). Set `dicomweb_backend = "external"` and `dicomweb_external_root = "/pacs-web"` in the project's `settings.toml`; recommend `dicomweb_enabled = false` (no builtin router).
2. **`orthanc.json`** — DICOMweb plugin enabled; storage area = cache dir; a modality entry for the real PACS; **`RemoteAccessAllowed: false`** / bind `127.0.0.1`; DICOMweb **public root** set so emitted BulkDataURIs are `{base_path}/pacs-web/...` (route back through nginx).
3. **Lua caching proxy** — on QIDO/WADO miss, pull study/series from the real PACS (C-MOVE/C-GET), **serve first, persist in background**; TTL + size housekeeping mapping `dicomweb_cache_ttl_hours=24` / `dicomweb_cache_max_size_gb=10` to Orthanc storage limits / a cleanup job.
4. **nginx** — the `{base_path}/pacs-web/` proxy + the `auth_request` block from spec §4:
   - `auth_request /_clarinet_authz;` → internal `location = /_clarinet_authz` → `proxy_pass http://127.0.0.1:8000{base_path}/api/auth/session/validate;` with `proxy_pass_request_body off;`, `proxy_set_header Content-Length "";`, `X-Real-IP`/`X-Forwarded-For` forwarded, and `proxy_cache_valid 200 10s;` keyed by `$cookie_clarinet_session`.
   - inject a shared-secret header to Orthanc; strip inbound auth headers.
5. **Speed notes** — external data-source flags default to `true` (Task 2); enable Orthanc transfer-syntax transcoding / threaded retrieval.
6. **Header** — docs-only, not installer-wired, not CI-tested.

- [ ] **Step 2: Commit**

```bash
git add docs/orthanc-dicomweb-proxy.md
git commit -m "docs(dicomweb): Orthanc + Lua + nginx caching-proxy reference config"
```

---

## Final verification

- [ ] Backend suite: `uv run pytest tests/test_dicomweb_settings.py tests/test_ohif_app_config.py tests/test_info_endpoint.py -v > /tmp/test-dicomweb-backend.txt 2>&1` — all pass.
- [ ] `make check > /tmp/check-dicomweb-backend.txt 2>&1` — format + lint + typecheck clean (new `bool | None` settings + `model_validator` typed).
- [ ] Frontend: `make frontend-check` clean, `make frontend-build` produces the bundle.
- [ ] Manual smoke: with `dicomweb_backend="builtin"`, `GET /ohif/app-config.js` shows `qidoRoot: "/dicom-web"` and `qidoSupportsIncludeField: false` (unchanged behavior); flip to `external` + `dicomweb_external_root="/pacs-web"`, restart, confirm `qidoRoot: "/pacs-web"` and `true` flags.
- [ ] Run pr-diff-reviewer before the first `gh pr create` (project rule).

## Self-Review notes

- **Spec coverage:** §1 Settings → Task 1; §2 rendering → Tasks 2-3; §3 info + preload gating → Tasks 4-5; §4 auth → Task 6 (nginx; Clarinet side confirmed needs no new endpoint — `read_token` already commits `last_accessed`, so `/api/auth/session/validate` is a complete authz target); §5 reference doc → Task 6; §6 tests → embedded per task; §7 compat (default builtin, no migration) → Global Constraints + Task 1 default; §8 out-of-scope (anon/archive for external) → not implemented, called out in the reference doc.
- **Type consistency:** `dicomweb_backend` is a `str` end-to-end (Python `Literal` ⊂ str; Gleam `String`; `ohif_preload_enabled` compares to `"builtin"`). Renderer kwargs match between `render_datasources_js` and the `serve_spa` call site.
- **Placeholders:** none — every code step is concrete.
