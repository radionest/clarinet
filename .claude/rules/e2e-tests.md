---
description: E2E test context — frontend stack, VM sub-path, selectors
paths:
  - "deploy/test/e2e/**"
---

## Frontend stack

Frontend is **Gleam/Lustre** (not React/Vue/Svelte). Source of truth for HTML selectors:
- Pages: `clarinet/frontend/src/pages/` (`.gleam` files)
- Components: `clarinet/frontend/src/components/`
- Login form: `clarinet/frontend/src/pages/login.gleam`

## VM sub-path deployment

App is always deployed behind a sub-path prefix (e.g. `/liver_nir/`).
- `PATH_PREFIX` is defined in `deploy/vm/vm.conf` — single source of truth
- Makefile loads it via `. deploy/vm/vm.conf` and passes as `CLARINET_TEST_URL`
- If tests can't find the SPA — check that `PATH_PREFIX` is loaded correctly
- Never curl the VM root (`/`) — always use the sub-path

## Running

```bash
make vm-e2e          # Playwright tests (requires running VM)
make vm-acceptance   # pytest acceptance tests (requires running VM)
```
