# Frontend Development Guide

## Technology Stack

- **Gleam**: Functional language with type safety — pure Gleam, no JavaScript FFI
- **Lustre** (~> 5.6): Elm-inspired web framework, MVU (Model-View-Update) architecture
- **Modem** (~> 2.1): Client-side routing
- **Formosh**: Form handling (private: `git@github.com:radionest/gleam_formosh.git`)
- **Plinth** (~> 0.7.2): DOM manipulation
- **gleam_fetch** (~> 1.3): HTTP requests with automatic cookie handling
- **gleam_javascript** (~> 1.0): JavaScript interop utilities
- **multipart_form** (~> 1.0): Multipart form encoding (login)
- **gleeunit** (~> 1.4): Test runner (dev dependency)

## Directory Structure

Frontend is embedded at `clarinet/frontend/`. Entry point: `clarinet.gleam`.

```
clarinet/frontend/clarinet/
├── clarinet.gleam        # Entry point
├── main.gleam            # App initialization
├── router.gleam          # Client-side routing
├── store.gleam           # Global state
├── api/                  # HTTP client, models, types
├── components/           # Reusable UI (layout, forms/)
├── pages/                # App pages (home, login, records/, studies/, patients/, series/, users/)
└── utils/                # Helpers: DOM utilities, record permissions
```

## Building

Uses `lustre_dev_tools` (bun bundler) to produce a single minified JS bundle.
Entry point: `clarinet_frontend.gleam`. Output: `clarinet/static/clarinet_frontend.js`.

**Dev build:**
```bash
cd clarinet/frontend
gleam run -m lustre/dev build --outdir=../../clarinet/static
```

**Production build (minified):**
```bash
make frontend-build
# Or: ./scripts/build_frontend.sh
```

Output goes to `clarinet/static/`; FastAPI serves when `frontend_enabled=True` (default).
Set `frontend_enabled=False` in settings for API-only mode.

**Requires:** `bun` installed system-wide (`curl -fsSL https://bun.sh/install | bash`).

**Note:** `formosh` requires access to a private Git repository. You may need repo access or an alternative form handling solution.

## Authentication Architecture

**Backend (FastAPI-users):**
- Session-based auth; `AccessToken` model (UUID4) stored in DB
- Cookies: httpOnly, secure (prod), SameSite=lax; name: `clarinet_session`
- 24h expiry default; sliding refresh + absolute/idle timeouts
- Password hashing via bcrypt

**Frontend (Cookie-based):**
- Auth state = user presence in store
- gleam_fetch auto-includes session cookies — no manual token management
- Login: multipart/form-data (username/password)
- Logout: clears DB session + cookie
