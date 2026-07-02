# Orthanc as an external DICOMweb proxy for OHIF

**Reference config — not wired into the Clarinet installer, not covered by CI.** Adapt the
values to your deploy and verify on a staging stand before production.

This describes the `external` DICOMweb backend: a separate **Orthanc** on the same host,
reverse-proxied **same-origin** by nginx, acting as a caching DICOMweb proxy in front of the
real PACS. It replaces Clarinet's in-process `builtin` proxy (`clarinet/services/dicomweb/`)
for projects that select it. Motivation: native (C++) DICOMweb is much faster than the Python
proxy, and a **serve-first, persist-in-background** cache keeps first-byte latency low.

## 1. Clarinet side (already implemented)

In the project's `settings.toml` (or `CLARINET_*` env):

```toml
dicomweb_backend = "external"
dicomweb_external_root = "/pacs-web"   # same-origin path; base_path is prepended automatically
dicomweb_enabled = false               # recommended: don't mount the builtin /dicom-web router
```

Effect: `GET /ohif/app-config.js` renders OHIF's `dataSources` with
`qidoRoot = wadoRoot = wadoUriRoot = {base_path}/pacs-web` and the fast capability flags
(`qidoSupportsIncludeField`, `supportsFuzzyMatching`, `supportsWildcard` = `true`). The OHIF
preload widget is hidden in the frontend (it only warms the builtin cache). Changing the
backend is a settings edit + API restart — no `clarinet ohif install` re-run.

`dicomweb_enabled = false` un-mounts Clarinet's builtin `/dicom-web` proxy router (it is
mounted when `dicomweb_enabled = true`, the default). With the `external` backend OHIF never
calls `/dicom-web`, so leaving it mounted only keeps an unused, PACS-reachable endpoint
exposed — disable it unless you still rely on the builtin archive/preload endpoints. The two
settings are intentionally independent; nothing auto-disables the router for you.

`{base_path}` is the sub-path the instance is deployed under (e.g. `/liver_nir`), empty for a
root deploy. Below, substitute it wherever `{base_path}` appears.

## 2. Orthanc (`orthanc.json`)

Run Orthanc bound to localhost only; expose it solely through nginx.

```jsonc
{
  "Name": "ClarinetProxy",
  // Bind to loopback — never expose Orthanc directly.
  "HttpServer": { "Enabled": true },
  "RemoteAccessAllowed": false,
  "HttpPort": 8042,
  "DicomAet": "CLARINETPROXY",

  // Local storage acts as the cache (see §3 for TTL/size housekeeping).
  "StorageDirectory": "/var/lib/orthanc-proxy/cache",
  "IndexDirectory": "/var/lib/orthanc-proxy/index",

  // Upstream PACS that holds the real images (Clarinet's pacs_host/port/aet).
  "DicomModalities": {
    "pacs": ["PACS_AET", "pacs.host.example", 104]
  },

  "DicomWeb": {
    "Enable": true,
    // IMPORTANT: set Root to the PUBLIC prefix so the BulkDataURIs Orthanc emits in
    // WADO-RS metadata route back through nginx (and thus through auth — §4), not to
    // 127.0.0.1:8042. Must equal {base_path}/pacs-web/ . See the §4 nginx note: do NOT
    // strip the prefix in proxy_pass when Root carries it.
    "Root": "{base_path}/pacs-web/",
    "EnableWado": true,
    "WadoRoot": "{base_path}/pacs-web/wado"
  },

  // Lua proxy that pulls-on-miss + serves-first (see §3).
  "LuaScripts": [ "/etc/orthanc/proxy.lua" ]
}
```

**Verify after deploy:** request `GET {base_path}/pacs-web/studies/<uid>/series/<uid>/metadata`
through the browser/OHIF and confirm every `BulkDataURI` begins with `{base_path}/pacs-web/`.
If they come back as `http://127.0.0.1:8042/...` or `/dicom-web/...`, the `Root`/forwarding is
misconfigured — fix it before going live (wrong prefix = broken images **and** an auth bypass).

## 3. Lua caching proxy (`proxy.lua`)

Behavior to implement (mirrors Clarinet's builtin proxy): on a QIDO/WADO request for a
study/series Orthanc does **not** yet hold, pull it from the upstream PACS, **serve the
response as soon as the data is available, and persist to the local cache in the background /
in parallel** — do not block the response on the disk write. Subsequent requests hit the
local cache.

Sketch (adapt to your Orthanc version's Lua API):

```lua
-- On an incoming DICOMweb request whose study/series is absent locally, trigger a
-- C-MOVE/C-GET retrieve from the upstream 'pacs' modality, then let Orthanc serve.
-- Retrieve is fire-and-forget for caching; the first response may come straight from
-- the retrieved in-memory instances (serve-first), with the store completing after.
function OnMissingResource(level, uid)
  -- RestApiPost('/modalities/pacs/move' or '/query' ...) to fetch `uid`
  -- Return as soon as instances are retrievable; persistence happens via Orthanc's
  -- normal store path (background).
end
```

**Cache housekeeping — match Clarinet's current limits** (`dicomweb_cache_ttl_hours = 24`,
`dicomweb_cache_max_size_gb = 10`):

- **Size cap:** set Orthanc `"MaximumStorageSize": 10240` (MB) so it evicts oldest studies
  when the cache exceeds 10 GB. (Or `"MaximumPatientCount"` if you prefer count-based.)
- **TTL (24 h):** Orthanc has no native per-study TTL; run a cron/systemd-timer that deletes
  studies whose `LastUpdate` is older than 24 h via the REST API
  (`GET /studies` → filter by `MainDicomTags`/metadata → `DELETE /studies/<id>`).

## 4. nginx — same-origin proxy + auth

Add to the Clarinet server block (`deploy/nginx/clarinet.conf`). The session cookie
(`clarinet_session`) rides along same-origin; an `auth_request` subrequest validates it
against Clarinet before any image is served. **Cache the subrequest** so per-frame auth cost
is near zero.

```nginx
# Cache for the auth subrequest (declare once, http{} scope).
proxy_cache_path /var/cache/nginx/clarinet_authz keys_zone=authz:1m max_size=10m inactive=60s;

# --- DICOMweb proxy to the local Orthanc ---
location {base_path}/pacs-web/ {
    auth_request /_clarinet_authz;          # 401 -> denied; OHIF surfaces an auth error

    # NO trailing slash on proxy_pass: preserve the full path so Orthanc's DicomWeb.Root
    # (= {base_path}/pacs-web/) matches and emitted BulkDataURIs keep the public prefix.
    proxy_pass http://127.0.0.1:8042;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host  $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    # Defense in depth: a shared secret Orthanc requires; strip any inbound auth header.
    proxy_set_header Authorization "";
    # proxy_set_header X-Orthanc-Shared-Secret "<secret>";   # enforce in Orthanc if used
}

location = /_clarinet_authz {
    internal;
    proxy_pass http://127.0.0.1:8000{base_path}/api/auth/session/validate;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Real-IP        $remote_addr;          # preserve client IP (ip-binding)
    proxy_set_header X-Forwarded-For  $proxy_add_x_forwarded_for;
    proxy_cache authz;
    proxy_cache_key $cookie_clarinet_session;
    proxy_cache_valid 200 10s;                               # ~1 FastAPI hit / 10s / session
}
```

Notes:
- The auth target `GET {base_path}/api/auth/session/validate` is read-only and returns
  200/401/403; `auth_request` inspects status only. `read_token` commits `last_accessed`,
  so a cached validate every ~10 s keeps an actively-viewing session non-idle and visible
  in presence.
- **Client IP / `session_ip_check`.** `session_ip_check` is **off by default**
  (`settings.py`). When you enable it, Clarinet validates `request.client.host`; forward
  `X-Real-IP`/`X-Forwarded-For` on the subrequest so an IP-bound session sees the real
  browser IP, not nginx's loopback address (uvicorn trusts the loopback proxy's forwarded
  IP by default). With the check off, forwarding these headers is harmless
  (logging / future use), not a functional auth requirement.
- **Revocation window.** The `proxy_cache_valid 200 10s` authz cache, plus Clarinet's
  in-memory session cache (`session_cache_ttl_seconds`, default 30 s), mean a just-revoked
  or just-expired session can still pull images from Orthanc for up to
  ~max(10 s, `session_cache_ttl_seconds`). This is a deliberate latency/load tradeoff on the
  image hot path; lower both values if you need near-instant revocation.
- **Cookie name.** `proxy_cache_key $cookie_clarinet_session` assumes the default
  `settings.cookie_name = "clarinet_session"`. If you override `cookie_name`, update the
  cache key to match.

## 5. Speed

The win is Orthanc (C++) + serve-first caching; the OHIF data-source flags default to the
fast settings for `external` (see §1). Additionally consider: Orthanc transfer-syntax
transcoding to a web-friendly syntax, and OHIF prefetch. The auth subrequest is cached, so it
does not sit in the per-frame hot path.

## 6. Operations — switching a running deployment

Concrete sequence to move an **already-deployed** Clarinet instance from `builtin` to
`external`. Paths below match the systemd deploy (`deploy/`): API unit `clarinet-api.service`,
`WorkingDirectory=/opt/clarinet`, stand overrides in `/opt/clarinet/settings.custom.toml`
(loaded over the project's `settings.toml`). Substitute `{base_path}` with the instance's
`root_url` (e.g. `/liver_nir`, empty for a root deploy) and `HOST` with the public hostname,
wherever they appear below. Steps 1–2 are one-time Orthanc/nginx setup — skip them if those are
already in place; steps 0 and 3–5 are the per-instance switch.

**0. Prereq — refresh the OHIF template (when the runtime `app-config.js` predates this
feature).** The serve-time renderer needs the `__CLARINET_DATASOURCES__` sentinel in the
runtime `app-config.js`. An OHIF dir installed before the external-backend feature lacks it
(the API then logs a warning and serves the config **unrendered**), so refresh it once on the
first switch:

```bash
sudo -u clarinet /opt/clarinet/venv/bin/clarinet ohif install --force-config
```

> `--force-config` overwrites the runtime `app-config.js` with the packaged template — any
> hand-edits to it (e.g. a customized `customizationService`) are lost; re-apply them after.

**1. Stand up the Orthanc proxy** per §2 (`orthanc.json`, localhost-bound,
`DicomWeb.Root = {base_path}/pacs-web/`) and §3 (`proxy.lua` + cache limits). Test it on a
staging stand first — this is reference config, not a turnkey artifact.

**2. Add the nginx location** per §4 (`{base_path}/pacs-web/` + `auth_request` +
`/_clarinet_authz` + `proxy_cache_path`), then reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

**3. Switch the Clarinet backend** — add the §1 settings to `/opt/clarinet/settings.custom.toml`
(see §1 for the rationale). `dicomweb_enabled = false` is **recommended, not required** — the
backend switches without it; it only un-mounts the now-unused builtin `/dicom-web` router:

```toml
dicomweb_backend = "external"
dicomweb_external_root = "/pacs-web"
dicomweb_enabled = false   # recommended (§1): un-mount the unused builtin /dicom-web
```

**4. Restart the API** (workers do not need a restart for this):

```bash
sudo systemctl restart clarinet-api
```

`app-config.js` re-renders from the new settings on the next request — no `ohif install` re-run.

**5. Verify.**

```bash
# OHIF now points at the external root:
curl -s https://HOST{base_path}/ohif/app-config.js | grep -E 'qidoRoot|wadoRoot'
#   → "{base_path}/pacs-web"

# the proxied path requires a session (no cookie → 401):
curl -s -o /dev/null -w '%{http_code}\n' https://HOST{base_path}/pacs-web/studies
#   → 401
```

Then open a study in OHIF: QIDO/WADO requests go to `{base_path}/pacs-web/...` and return 200,
images load, and a WADO-RS metadata `BulkDataURI` begins with `{base_path}/pacs-web/` (not
`127.0.0.1:8042`).

**Rollback (instant).** Set `dicomweb_backend = "builtin"` (or remove the three lines) and
`sudo systemctl restart clarinet-api` — back to the builtin proxy. nginx/Orthanc may stay up;
OHIF simply stops calling `/pacs-web`. Users with an open OHIF tab should hard-refresh to drop
a browser-cached `app-config.js`.

## Out of scope (see the design spec §8)

- **Anonymization for external:** the builtin proxy serves anonymized DICOM from `dcm_anon/`.
  For Orthanc, anonymized studies must be *delivered* into Orthanc (Clarinet's
  `anon_send_to_pacs`) or proxied from an anonymized PACS — a separate task.
- **Archive ZIP / preload** parity for the external backend.
