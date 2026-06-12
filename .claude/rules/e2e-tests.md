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

## Quarto reports E2E

`test_quarto_reports.py` covers `/admin/quarto-reports`:
- `test_quarto_reports_page_loads` — always runs (page + sub-path prefix).
- `test_quarto_render_to_docx` — full render → poll → download; **skips** only
  when no `*.qmd` templates are configured on the VM. Once the render is
  dispatched, a failure or timeout **fails** the test — templates without the
  `quarto` CLI/Jupyter kernel means a misprovisioned VM, not an absent feature.

The test VM is provisioned automatically by `vm.sh cmd_deploy`: it ships the
Quarto tarball (host-cached in `~/.cache/clarinet-deploy/`), the `quarto` pip
extra wheels, and the demo fixtures from `deploy/test/fixtures/quarto/`
(`review/*.qmd` + `*.sql` → `/opt/clarinet/review/`, plus a downstream-style
`.env.example` → `/opt/clarinet/`), then restarts the services. On a freshly
imaged VM the render test therefore runs for real — a skip means the
provisioning step regressed. Smoke block `[7] Quarto CLI` additionally runs
`clarinet quarto status` from `/opt/clarinet` (with the planted `.env.example`
this regression-tests the neutral-cwd fix in `quarto_status`).

For an arbitrary (non-test) deployment the render test still skips unless the
same pieces are provisioned by hand:
- the `quarto` CLI — `clarinet quarto install` (or `--from-file <tarball>` offline);
- the `quarto` pip extra in the app venv — `jupyter`, `ipykernel`, `pandas`;
- a `*.qmd` in `settings.quarto_reports_path` (default `./review/`) whose
  `clarinet.data` report names resolve to `*.sql` files in `settings.reports_path`.

Production bundles without a Quarto tarball are unaffected (Quarto stays an
optional, heavy feature). See `docs/quarto-reports.md`.
