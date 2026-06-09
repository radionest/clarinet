# Quarto Reports

Quarto reports let a project author write a narrative document in
[Quarto](https://quarto.org/) (`*.qmd` — Markdown plus executable Python code
chunks), pull tabular data from the existing **SQL reports**, and render it to
**DOCX** (primary) or **PDF**. Rendering runs as a background job; admins
download the result from the **Quarto** tab once it finishes.

It mirrors the SQL *reports* feature (`*.sql` → CSV/XLSX) but produces a
formatted document instead of a spreadsheet.

## Authoring a report

Drop a `*.qmd` file into the project's reports folder
(`settings.quarto_reports_path`, default `./review/` — the same folder as the
`*.sql` reports). The file is discovered at API startup; **restart the API to
pick up new or changed files** (same model as SQL reports / record types).

The YAML front matter supplies the UI metadata and declares which SQL reports
to materialize as data:

```yaml
---
title: "Monthly Summary"
description: "Records grouped by status, with a chart."
clarinet:
  data:
    - monthly_summary      # name of a *.sql report (stem, no extension)
    - user_stats
---
```

Each name under `clarinet.data` must match a `*.sql` report in
`settings.reports_path`. Before rendering, the backend executes each one
(read-only, with the same `SELECT`/`WITH` validation and statement timeout as
the SQL reports feature) and writes the result to `data/<name>.csv` in the
render working directory. A typo here is reported as a `404` when the render is
requested, not as a silent failure later.

A Python chunk then reads the CSV like any local file:

````markdown
```{python}
import pandas as pd

df = pd.read_csv("data/monthly_summary.csv")
df.head()
```
````

Because the data is a plain CSV, the chunk never opens a database connection
and never needs credentials.

## Rendering & download (UI / API)

- **List:** `GET /api/admin/quarto-reports`
- **Render (background):** `POST /api/admin/quarto-reports/{name}/render`
  with `{"formats": ["docx"]}` (or `["docx","pdf"]`). Returns a pending render
  state including a `render_id`.
- **Poll:** `GET /api/admin/quarto-reports/{name}/renders/{render_id}/status`
  → `{status: pending|running|done|failed, error?, ...}`.
- **Download:** `GET /api/admin/quarto-reports/{name}/renders/{render_id}/download?format=docx`
  (409 until the render is `done`).

All endpoints require admin (`is_superuser` or the `admin` role).

Render state is stored as a `status.json` sidecar next to the output under
`{storage_path}/quarto_renders/<name>/<render_id>/` — there is **no database
table**. When `pipeline_enabled` is true the render runs on a pipeline worker;
otherwise it runs in-process via `asyncio.create_task`. Either way the API and
worker must share the storage filesystem (they already do for output files).

## Installing the Quarto CLI

Quarto is **not** a pip package — it is a self-contained binary that bundles
its own pandoc and typst, so **no system pandoc and no LaTeX are required**
(DOCX via the bundled pandoc, PDF via the bundled typst). Install it with the
CLI, which mirrors `clarinet ohif install`:

```bash
# Online: download the version from settings (settings.quarto_default_version)
uv run clarinet quarto install

# Air-gapped (e.g. Astra Linux): copy a tarball over, then install from it
uv run clarinet quarto install --from-file ./quarto-1.4.557-linux-amd64.tar.gz

# Verify (also runs `quarto check`)
uv run clarinet quarto status
```

The binary lands under `{storage_path}/quarto`. Resolution order at render
time: `settings.quarto_executable` (explicit) → `{storage_path}/quarto/bin/quarto`
→ `quarto` on `PATH`.

For the Python chunks to execute you also need a Jupyter kernel in the worker's
environment:

```bash
uv sync --extra quarto      # installs jupyter + ipykernel + pandas
```

### Astra Linux / older glibc

Quarto's bundled `deno`/`pandoc` are built against a relatively recent glibc.
On older targets (e.g. **Astra Linux SE 1.7 / Smolensk**) a current Quarto may
fail with `GLIBC_x.y not found`. The default
`settings.quarto_default_version` is pinned conservatively (`1.4.x`); pick a
version that runs on your host and verify with `clarinet quarto status`
(it runs `quarto check`). Override the version per install with
`--version`/`--from-file` or set `CLARINET_QUARTO_EXECUTABLE`.

## Security — trust boundary

> **Python chunks in `.qmd` files run with the privileges of the Clarinet
> worker process. Authors must be trusted project operators** — the same trust
> already required to write a `*.sql` report.

Mitigations the framework applies:

- The `quarto render` subprocess gets a **minimal environment built from
  scratch** — `CLARINET_*`, `DATABASE_URL`, the service token and AMQP
  credentials are **never** passed through. `HOME`/`XDG_*`/`TMPDIR` are
  redirected into the per-render directory.
- Data reaches chunks only as pre-rendered CSV files; chunks have no DB access.
- SQL data is read through the SQL-report repository (read-only transaction,
  `SELECT`/`WITH`-only, statement timeout).
- The render is time-boxed by `settings.quarto_render_timeout_seconds`.

Do **not** accept `.qmd` files from untrusted users. For stronger isolation,
run the worker under a dedicated low-privilege OS user (an operational measure,
not enforced by the framework).

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `quarto_reports_path` | `./review/` | Folder scanned for `*.qmd` |
| `quarto_render_timeout_seconds` | `600` | Per-render wall-clock limit |
| `quarto_default_version` | `1.4.557` | Version used by `clarinet quarto install` |
| `quarto_executable` | `None` | Explicit binary path (overrides auto-resolve) |
| `quarto_output_path` | `None` | Output root (default `{storage_path}/quarto_renders`) |
