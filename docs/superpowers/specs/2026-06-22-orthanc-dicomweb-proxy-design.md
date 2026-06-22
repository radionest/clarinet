# Per-project DICOMweb backend for OHIF (builtin ↔ external Orthanc)

**Date:** 2026-06-22
**Status:** Design approved, pending spec review
**Branch:** `worktree-dicomweb-backend`

## Goal

Let a Clarinet project choose which **DICOMweb backend** OHIF talks to, and pass that
backend's parameters into OHIF's `app-config.js`. Two backends coexist:

- `builtin` — the current Python proxy (`clarinet/services/dicomweb/`, router `/dicom-web`).
  Default; existing projects unchanged.
- `external` — a separate **Orthanc** on the same host, fronted by **nginx** (same-origin),
  acting as a caching DICOMweb proxy. Motivation: the builtin proxy is slow and hard to
  maintain; Orthanc (C++ DICOMweb) + serve-first caching is much faster. **Image-delivery
  latency is the primary driver.**

Orthanc/Lua setup itself is **out of the codebase** — shipped as a documented reference
config under `docs/` (not wired into the installer, not CI-tested).

## Locked decisions

1. **Scope:** Clarinet code + reference Orthanc/Lua config in `docs/`. No deploy automation.
2. **Coexistence:** `builtin` and `external` both supported; project picks via settings.
3. **Topology (external):** same-origin — nginx on the Clarinet host reverse-proxies a path
   (e.g. `/pacs-web`) to localhost Orthanc. OHIF uses a relative root → session cookie works,
   no CORS. Absolute cross-origin URLs are out of scope.
4. **Orthanc proxy model (docs reference):** caching proxy, **serve-first then persist**
   (background/parallel), on-demand pull from the real PACS, cache TTL/size ≈ current
   (`dicomweb_cache_ttl_hours=24`, `dicomweb_cache_max_size_gb=10`). Mirrors builtin behavior.
5. **OHIF config delivery:** **dynamic render at serve time** (Approach B) — `serve_spa`
   special-cases `ohif/app-config.js` and substitutes the `dataSources` block from settings,
   cached in-memory. Change backend = edit `settings.toml` + restart; no OHIF reinstall.
6. **Speed is critical** — external data-source flags are tuned for Orthanc (see §2).
7. **Auth (external):** nginx `auth_request` → `/api/auth/session/validate` with short-TTL
   subrequest caching. Clarinet stays the single session authority; image bytes stream from
   Orthanc on the hot path, no Python hop (see §4).

## Current state (anchors)

| What | Where |
|---|---|
| Builtin proxy router mount | `clarinet/api/app.py:602-605` (`if settings.dicomweb_enabled`) |
| OHIF static/SPA serving | `clarinet/api/app.py:642-667` (`serve_spa`, ohif branch `:652`) |
| index.html templating precedent | `clarinet/api/app.py:630-639` (`_render_index`, `$BASE_PATH`/`$PROJECT_TITLE`) |
| OHIF asset patcher | `clarinet/cli/main.py:666-724` (`_patch_ohif_paths`); app-config stanza `:717-724` |
| OHIF config template (static today) | `clarinet/ohif/app-config.js` — `dataSources` hardcodes `/dicom-web` (`:33-52`) |
| `/api/info` | `clarinet/api/routers/info.py:21` (`"viewers": registry.viewer_info()`) |
| Viewer registry build | `clarinet/api/app.py:325-330`, `clarinet/services/viewer/registry.py` |
| Frontend viewer/preload | `clarinet/frontend/src/utils/viewer.gleam` (`ohif_record_button` → `on_view` → preload) |
| Frontend info decode | `clarinet/frontend/src/api/info.gleam:34` |
| dicomweb settings | `clarinet/settings.py:235-251` |

## Design

### 1. Settings — `clarinet/settings.py`

Add alongside the existing flat `dicomweb_*` block (`CLARINET_`-prefixed):

| Setting | Type | Default | Notes |
|---|---|---|---|
| `dicomweb_backend` | `Literal["builtin","external"]` | `"builtin"` | Selects OHIF data source. Default = no behavior change. |
| `dicomweb_external_root` | `str \| None` | `None` | Same-origin path (e.g. `/pacs-web`). **Required** when `external`. Used as `qidoRoot`/`wadoRoot`/`wadoUriRoot`. Prefixed with deploy base_path at render. |
| `dicomweb_friendly_name` | `str` | `"Clarinet PACS"` | OHIF `friendlyName`. |
| `dicomweb_qido_supports_include_field` | `bool \| None` | `None` | `None` ⇒ per-backend default. |
| `dicomweb_supports_fuzzy_matching` | `bool \| None` | `None` | `None` ⇒ per-backend default. |
| `dicomweb_supports_wildcard` | `bool \| None` | `None` | `None` ⇒ per-backend default. |

**Per-backend defaults** (when the bool is `None`): `builtin` → `False` (matches today's static
config; Python proxy can't do these); `external` → `True` (Orthanc supports them; fewer round
trips → faster). `imageRendering`/`thumbnailRendering` stay `"wadors"` (constant, not a setting — YAGNI).

**Validation (fail-fast at startup):** `backend == "external"` with no `dicomweb_external_root`
→ raise (same pattern as other config checks; surfaces as `StartupError`). Settle flat-vs-nested
during spec review; flat chosen here for consistency with `dicomweb_*`.

### 2. app-config.js rendering (Approach B)

**Template** — `clarinet/ohif/app-config.js`: replace the literal `dataSources: [...]` value
(`:33-50`) with a sentinel, keep everything else (customizationService, mouse-binding IIFE) as
real, hand-editable code:

```js
  dataSources: $DICOMWEB_DATASOURCES,
  defaultDataSourceName: 'dicomweb',
```

`routerBasename: '/ohif'` **stays** and continues to be patched at install (`_patch_ohif_paths`,
deploy base_path is fixed per instance — no need to make it dynamic).

**Renderer** — new `_render_app_config(...)` next to `_render_index` in `app.py`:
- Read the **installed** runtime file `ohif_dir/app-config.js` (preserves operator hand-edits to
  customizationService; already basename-patched at install).
- Substitute the `$DICOMWEB_DATASOURCES` sentinel with the rendered block (below). Cache the
  result in-memory keyed by base_path (mirror `_index_html_cache`).
- Fallback: if the sentinel is absent (app-config installed by a pre-feature Clarinet), log a
  warning and serve the file unchanged; document that `clarinet ohif install --force-config`
  refreshes the template.

**Rendered `dataSources` block** (single source `sourceName: 'dicomweb'` for both backends):

```js
[{ namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
   sourceName: 'dicomweb',
   configuration: {
     friendlyName: <dicomweb_friendly_name>, name: 'clarinet',
     wadoUriRoot: <ROOT>, qidoRoot: <ROOT>, wadoRoot: <ROOT>,
     qidoSupportsIncludeField: <resolved>, imageRendering: 'wadors',
     thumbnailRendering: 'wadors', supportsFuzzyMatching: <resolved>,
     supportsWildcard: <resolved> } }]
```

`<ROOT>`:
- `builtin` → `{base_path}/dicom-web`
- `external` → `{base_path}{dicomweb_external_root}` (relative path; base_path-prefixed like `/dicom-web` is today)

**Serve** — `serve_spa` (`app.py:652` ohif branch): intercept `full_path == "ohif/app-config.js"`
**before** the static `FileResponse` and return the rendered content (`media_type="application/javascript"`).
All other OHIF files served statically as today.

**Installer** — `_patch_ohif_paths` (`cli/main.py:717-724`): **remove** the `'/dicom-web'` →
`{base_path}/dicom-web` replacement (the path now comes from the rendered dataSources); **keep**
the `routerBasename` replacement.

### 3. `/api/info` + frontend preload gating

The builtin preload widget warms the **builtin** cache → meaningless for `external`. Gate it.

- **Backend:** `info.py:21` — add `"dicomweb_backend": settings.dicomweb_backend`.
- **Frontend:** `api/info.gleam:34` decode the field into `Shared`
  (`shared.gleam`); thread a `preload_enabled = backend == "builtin"` bool into
  `viewer.gleam` (`record_viewer_buttons` / `ohif_record_button`). When `False`, OHIF opens via a
  plain link (no `StartPreload`), like the other viewers. OHIF launch URL (`ohif_url`) is unchanged.

### 4. Authentication (external backend) — nginx `auth_request`

Builtin is unchanged (`CurrentUserDep`). For `external`, image bytes stream **directly** from
Orthanc (no Python hot path), so session validation runs at the nginx edge, not in FastAPI.

**Flow** — new nginx `location {base_path}/pacs-web/`:

```nginx
auth_request /_clarinet_authz;              # subrequest forwards the clarinet_session cookie
# 200 -> proxy_pass http://127.0.0.1:8042/dicom-web/...   (Orthanc, localhost)
# 401 -> return 401 (OHIF surfaces an auth error)

location = /_clarinet_authz {               # internal
  internal;
  proxy_pass http://127.0.0.1:8000{base_path}/api/auth/session/validate;
  proxy_pass_request_body off;  proxy_set_header Content-Length "";
  proxy_set_header X-Real-IP $remote_addr;              # preserve client IP (ip-binding)
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_cache authz;  proxy_cache_key $cookie_clarinet_session;
  proxy_cache_valid 200 10s;                            # bound FastAPI hits to ~1/10s/session
}
```

**Auth target:** reuse `GET /api/auth/session/validate` (`auth.py:77-104`, read-only, 200/401/403).
`auth_request` inspects status only.

**Clarinet-side verify-at-impl:** `DatabaseStrategy.read_token` enforces `idle_timeout` on
`last_accessed`. DICOMweb traffic bypasses FastAPI, so active viewing refreshes activity only via
the (cached) authz subrequest. Confirm `read_token` updates `last_accessed`; if it doesn't — or if
idle/presence accuracy during long viewing matters — add a thin dedicated endpoint
(`GET /api/auth/authz` → 204/401, marks activity, no body) tuned for `auth_request` and point nginx
at it instead.

**Hardening (goes in the reference doc, §5):**
- Orthanc binds `127.0.0.1` only; nginx injects a shared-secret header Orthanc requires; strip
  inbound auth headers before proxying to Orthanc.
- Orthanc DICOMweb **public root** = `{base_path}/pacs-web/` so WADO-RS BulkDataURI/retrieve URLs
  route back through nginx (→ re-auth), not to `127.0.0.1:8042` (else broken links + auth bypass).

### 5. Reference config — `docs/orthanc-dicomweb-proxy.md` (new, docs-only)

- `orthanc.json`: DICOMweb plugin enabled, storage area = cache dir, modality entry for the real PACS.
- **Lua** caching proxy: on QIDO/WADO miss, pull study/series from the real PACS (C-MOVE/C-GET),
  **serve first, persist in background**; TTL + size housekeeping mapping `dicomweb_cache_ttl_hours`
  / `dicomweb_cache_max_size_gb` to Orthanc storage limits / cleanup.
- **nginx** snippet: the `/pacs-web` proxy + the `auth_request` block from §4 + Orthanc
  localhost-bind / shared-secret / public-root / IP-forward hardening.
- **Speed notes:** enable the fast data-source flags (§1 external defaults), transfer-syntax
  transcoding, progressive/threaded retrieval.
- Header: docs-only, not installer-wired, not CI-tested.

### 6. Tests

- Unit: `_render_app_config` for `builtin` and `external` — correct `<ROOT>` (incl. base_path
  prefix) + resolved flags; sentinel-absent fallback; `external`-without-root fail-fast.
- Unit: `/api/info` includes `dicomweb_backend`.
- Frontend (gleeunit): preload gating — OHIF button is a plain link when backend ≠ builtin.
- Unit (only if a dedicated `GET /api/auth/authz` is added): 204 with valid session, 401 without.

### 7. Compat / migration

- Default `dicomweb_backend="builtin"` → existing projects render today's dataSources, no change.
- **No alembic migration** (settings only; Clarinet is framework — migrations are project-level).
- Upgrade edge: a runtime `app-config.js` from a pre-feature install lacks the sentinel → renderer
  serves it unchanged (warns); `clarinet ohif install --force-config` installs the new template.

### 8. Out of scope (flagged follow-ups)

- **Anonymization for external:** builtin serves anonymized DICOM from `dcm_anon/`. For Orthanc,
  anonymized studies must be *delivered* to Orthanc (existing `anon_send_to_pacs`, `settings.py:305`)
  or proxied from an anon PACS — separate task.
- **Builtin router when external:** keep `dicomweb_enabled` independent (default `True`). Operators
  typically set `dicomweb_enabled=false` for external (no archive/preload endpoints). Archive-ZIP /
  preload parity for external — not solved here.
