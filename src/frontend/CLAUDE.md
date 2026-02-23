# Frontend Development Guide

## Technology Stack

- **Gleam**: Functional language with type safety — pure Gleam, no JavaScript FFI
- **Lustre** (~> 5.4): Elm-inspired web framework, MVU (Model-View-Update) architecture
- **Modem** (~> 2.1): Client-side routing
- **Formosh**: Form handling (private: `git@github.com:radionest/gleam_formosh.git`)
- **Plinth** (~> 0.7.2): DOM manipulation
- **gleam_fetch** (~> 1.3): HTTP requests with automatic cookie handling

## Directory Structure

Frontend is embedded at `src/frontend/`. Entry point: `clarinet.gleam`.

```
src/frontend/src/
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

**Dev build:**
```bash
cd src/frontend
gleam deps download
gleam build --target javascript
```

**Production build:**
```bash
make frontend-build
# Or: ./scripts/build_frontend.sh
```

Output goes to `dist/`; FastAPI serves when `frontend_enabled=True` (default).
Set `frontend_enabled=False` in settings for API-only mode.

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
