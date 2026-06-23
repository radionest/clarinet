# Pipeline worker version gating

## Problem

Long-lived / forgotten pipeline workers run stale code — old `clarinet` and/or old
`plan/` (`config_tasks_path`) — yet keep consuming tasks from RabbitMQ and may execute
them with outdated logic / schema. Such workers must stop receiving tasks.

## Solution

Embed a **version fingerprint** into RabbitMQ queue names. The API publishes to queues
named with its fingerprint; a worker subscribes only to queues matching **its own**
fingerprint (computed from its loaded code). A stale worker therefore listens to dead,
unfed queues and receives nothing — immediately, with **zero race window**, enforced by
the broker rather than by worker logic. A one-shot startup diagnostic makes a stale
worker announce itself in the log.

## Decisions (locked)

| Question | Decision |
|---|---|
| Failure mode targeted | Long-lived / forgotten worker (skew detected *while running*) |
| Compatibility criterion | **Exact equality** of both versions |
| What "version" is | `clarinet` package version **+** content hash of all `plan/` files |
| Source of truth | Fingerprint of the **running API** (lives in the downstream deploy) |
| Enforcement mechanism | **Fingerprint in queue names** (broker-level isolation) |
| Stale-worker behavior | Silent standby on dead queues **+ one-shot startup ERROR** diagnostic |
| Kill switch | `pipeline_version_check_enabled` (default `True`) |

Rejected (with reason): per-message reject (hot-potato; worker still pulls), worker
registry + heartbeat (extra component, YAGNI), runtime poll loop / `self-check` (up to
poll-interval race window where stale worker runs new tasks with old code; more code).

## Key principle: fingerprint is a startup snapshot

Both API and worker compute the fingerprint **once at startup** and cache it
(`@lru_cache`). It reflects code **loaded into memory**, not the current files on disk —
so `git pull` without a restart cannot fake a match. A worker can only become compatible
again by restarting with new code (or the API rolling back to the worker's version).

Hash determinism across hosts: API and worker live in separate deploys/dirs. The hash is
over **file contents + relative paths** (not absolute paths), so identical code yields an
identical hash regardless of where `config_tasks_path` points.

## Components

### 1. `clarinet/services/pipeline/fingerprint.py` (new)

```python
def clarinet_version() -> str:
    # importlib.metadata.version("clarinet"); "unknown" on PackageNotFoundError
def compute_plan_hash(root: Path) -> str:
    # sha256 over sorted files under root: for each, update(rel_posix_path) + update(content).
    # Skip non-source artifacts that differ between deploys without meaning a code change:
    #   any path with "__pycache__"/".git" in parts; suffixes {.pyc,.pyo,.log,.swp,.tmp};
    #   names {.DS_Store}. Missing root -> hash of empty. (Blacklist is extendable.)
@lru_cache(maxsize=1)
def compute_fingerprint() -> str:
    # f"{clarinet_version()}:{compute_plan_hash(Path(settings.config_tasks_path))}"  — startup snapshot
@lru_cache(maxsize=1)
def queue_version_segment() -> str:
    # sha256(compute_fingerprint().encode()).hexdigest()[:12] — queue-name-safe segment
def reset_fingerprint_cache() -> None:
    # compute_fingerprint.cache_clear(); queue_version_segment.cache_clear() — for tests
```

Hash **all files** under `config_tasks_path` (`.py`, `.toml`, `.json`, `*.schema.json`, …),
not only `.py` — RecordType schemas/configs also change behavior. Determinism: sort by
relative POSIX path; include the path in the hash (catches renames/moves).

### 2. Queue names carry the segment — `clarinet/settings.py:445-468`

Add a private helper and route the four task-queue properties through it
(`default_queue_name`, `gpu_queue_name`, `dicom_queue_name`, `quarto_queue_name`):

```python
def _versioned_queue(self, kind: str) -> str:
    ns = self.pipeline_task_namespace
    if not self.pipeline_version_check_enabled:
        return f"{ns}.{kind}"                       # legacy / kill-switch
    from clarinet.services.pipeline.fingerprint import queue_version_segment
    return f"{ns}.{queue_version_segment()}.{kind}"
```

- **`dlq_queue_name` (`:466`) is NOT versioned** — stable terminal store so dead letters
  from all versions land together for inspection.
- `pipeline_task_namespace` (`:432`) unchanged — **task names** (`{ns}:{fn}`) stay stable
  so chain lookup (`GET /api/pipelines/{name}/definition`) is unaffected.
- Lazy import inside the method breaks the settings↔fingerprint import cycle.
- `delay`-queue is built as `f"{queue_name}.delay"` in `broker.py:86` → versioned
  automatically. `create_broker` (`broker.py:41`) needs no change — it receives final names.

### 3. API endpoint + lifespan warm-up

- `clarinet/api/routers/pipeline.py`: `GET /api/pipelines/fingerprint` →
  `{"fingerprint": compute_fingerprint()}`. **No auth** (worker-facing, mirrors existing
  `GET /api/pipelines/{name}/definition`). Works regardless of `pipeline_enabled`. Add a
  URL constant to `tests/utils/urls.py`.
- `clarinet/api/app.py` lifespan: call `compute_fingerprint()` **right after**
  `activate_plan_package(...)` (step 1c) so the cached snapshot reflects the code loaded at
  startup — not files edited later while the API runs. (When `pipeline_enabled`, broker
  creation already triggers it via queue names; the explicit call covers the disabled case
  and pins the snapshot moment.)

### 4. `ClarinetClient.get_worker_fingerprint()` — `clarinet/client.py`

Async GET to the endpoint, returns the fingerprint string. Pattern: existing `get_me`
(`client.py:358`) over the shared `_request` helper (`client.py:203`).

### 5. Worker startup diagnostic — `clarinet/services/pipeline/worker.py`

New testable helper, called from `run_worker` after `load_task_modules()` and after
`queues` is resolved (`worker.py:~195`), guarded by `settings.pipeline_version_check_enabled`:

```python
async def warn_if_stale(queues: list[str]) -> None:
    # client = ClarinetClient(base_url=settings.effective_api_base_url,
    #                         service_token=settings.effective_service_token)
    # try:
    #   api_fp = await client.get_worker_fingerprint(); mine = compute_fingerprint()
    #   if api_fp != mine: logger.error(f"Worker fingerprint {mine} != API {api_fp}; "
    #       f"listening on stale queues {queues} — will NOT receive new tasks until "
    #       f"code is updated and the worker restarted.")
    #   else: logger.info(f"Worker fingerprint matches API: {mine}")
    # except ClarinetAPIError as e (incl. 404 = old API w/o endpoint): logger.warning(...)
    # except (ConnectionError, TimeoutError, httpx errors): logger.warning(...)
    # finally: await client.aclose()   # close per existing client lifecycle (see task.py:80)
```

Diagnostic only — isolation is already enforced by queue names; never blocks startup.

### 6. Settings flag — `clarinet/settings.py`

`pipeline_version_check_enabled: bool = True` (near other `pipeline_*` settings, `:368`).
Add a one-line row to `.claude/rules/pipeline-ops.md` settings table.

## Operational notes (document, do not over-engineer)

- **Tasks at the version boundary.** A task published to the old-fingerprint queue right
  before a full upgrade orphans once all old workers are gone. Mitigation: empty stale
  queues self-clean (no publisher feeds them); reclaim via `x-expires` on task queues
  **or** extend `uv run clarinet rabbitmq clean` to drop consumer-less versioned queues.
  Pick one at implementation; default to extending `rabbitmq clean` (no TTL data-loss risk).
- **Bootstrap limit.** The gate only works once **both** API and worker run a version that
  has this feature. Pre-feature workers listen on un-versioned names (`{ns}.default`) and
  stop receiving the moment the new API publishes to `{ns}.{seg}.default` — but their
  leftover tasks in `{ns}.default` need draining by an old worker during the first deploy.
- `get_test_broker()` (`broker.py:183`, `InMemoryBroker`) is unaffected — tests bypass names.

## Tests (`tests/test_pipeline_fingerprint.py` new + additions)

- `compute_plan_hash`: determinism (same dir → same hash); sensitivity (edit a file →
  changes); ignores `__pycache__`/`*.pyc`/artifacts; order-independent (sorted); rename
  changes hash.
- `queue_version_segment`: 12-hex format, stable across calls, changes when plan changes.
- `clarinet_version`: returns a non-empty string, no raise.
- Queue properties: flag **on** → name has segment; **off** → legacy name. Use
  `monkeypatch` on `config_tasks_path` + `pipeline_version_check_enabled`, call
  `reset_fingerprint_cache()` before asserting.
- Endpoint: `GET /api/pipelines/fingerprint` → 200 + matches `compute_fingerprint()`.
- `warn_if_stale`: with `caplog`, mock `get_worker_fingerprint` → ERROR on mismatch, INFO
  on match, WARNING on `ClarinetAPIError`/connection error.
- Add an autouse fixture (or explicit calls) to `reset_fingerprint_cache()` so cached
  fingerprints don't leak between tests.

## Out of scope

Runtime re-checking, per-message version stamping, automatic old-queue GC beyond the one
chosen reclaim path, downstream DB migrations (none — not a schema change).
