# Pipeline Worker Version Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop stale pipeline workers (old `clarinet` and/or old `plan/` code) from receiving tasks by embedding a version fingerprint into RabbitMQ queue names.

**Architecture:** A fingerprint = `clarinet` package version + content hash of `config_tasks_path`, computed once at startup (`@lru_cache`). Queue names carry a short fingerprint segment, so a stale worker subscribes to dead (unfed) queues and receives nothing — isolation enforced by the broker, zero race window. A one-shot startup diagnostic logs ERROR when a worker's fingerprint differs from the running API's.

**Tech Stack:** Python 3.12, FastAPI, TaskIQ + aio-pika (RabbitMQ), httpx, pytest. All commands via `uv run`.

Spec: `docs/superpowers/specs/2026-06-22-pipeline-worker-version-gate-design.md`.

## Global Constraints

- Package name for `importlib.metadata.version(...)` is exactly `"clarinet"`.
- `settings.effective_api_base_url` already includes `/api` → `ClarinetClient` endpoints are written **without** `/api` (e.g. `/pipelines/fingerprint`).
- **DLQ (`dlq_queue_name`) is NOT versioned** — stable terminal store.
- **Task namespace (`pipeline_task_namespace`) is unchanged** — task names stay stable so chain lookup works.
- Kill switch `pipeline_version_check_enabled` defaults `True` in production, but is set `False` in the test settings fixture so pre-existing queue-name tests keep passing.
- Logger: `from clarinet.utils.logger import logger` (never loguru directly).
- Run tests redirected to a unique file, never piped: `> /tmp/test-pipeline-version-gate.txt 2>&1`. The **first** `uv run pytest` in this fresh worktree builds the venv — wrap it with `timeout 300`; later runs `timeout 120`.

---

### Task 1: Fingerprint module

**Files:**
- Create: `clarinet/services/pipeline/fingerprint.py`
- Create: `tests/test_pipeline_fingerprint.py`
- Modify: `clarinet/services/pipeline/__init__.py` (export public helpers)
- Modify: `tests/conftest.py` (autouse cache reset)

**Interfaces:**
- Produces:
  - `clarinet_version() -> str`
  - `compute_plan_hash(root: pathlib.Path) -> str`
  - `compute_fingerprint() -> str` (lru_cache) — `f"{version}:{plan_hash}"`
  - `queue_version_segment() -> str` (lru_cache) — 12 hex chars
  - `reset_fingerprint_cache() -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_fingerprint.py`:

```python
import hashlib
from pathlib import Path

import pytest

from clarinet.services.pipeline import fingerprint as fp


@pytest.fixture
def plan_dir(tmp_path: Path) -> Path:
    (tmp_path / "tasks.py").write_text("x = 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "flow.py").write_text("y = 2\n")
    return tmp_path


def test_compute_plan_hash_deterministic(plan_dir: Path) -> None:
    assert fp.compute_plan_hash(plan_dir) == fp.compute_plan_hash(plan_dir)


def test_compute_plan_hash_sensitive_to_content(plan_dir: Path) -> None:
    before = fp.compute_plan_hash(plan_dir)
    (plan_dir / "tasks.py").write_text("x = 2\n")
    assert fp.compute_plan_hash(plan_dir) != before


def test_compute_plan_hash_ignores_artifacts(plan_dir: Path) -> None:
    before = fp.compute_plan_hash(plan_dir)
    cache = plan_dir / "__pycache__"
    cache.mkdir()
    (cache / "tasks.cpython-312.pyc").write_bytes(b"\x00\x01")
    (plan_dir / "debug.log").write_text("noise\n")
    (plan_dir / ".DS_Store").write_bytes(b"\x00")
    assert fp.compute_plan_hash(plan_dir) == before


def test_compute_plan_hash_missing_root(tmp_path: Path) -> None:
    assert fp.compute_plan_hash(tmp_path / "nope") == hashlib.sha256().hexdigest()


def test_queue_version_segment_format() -> None:
    fp.reset_fingerprint_cache()
    seg = fp.queue_version_segment()
    assert len(seg) == 12
    assert all(c in "0123456789abcdef" for c in seg)


def test_clarinet_version_nonempty() -> None:
    assert fp.clarinet_version()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 300 uv run pytest tests/test_pipeline_fingerprint.py -v > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: FAIL — `ModuleNotFoundError: clarinet.services.pipeline.fingerprint` (the import).

- [ ] **Step 3: Write minimal implementation**

Create `clarinet/services/pipeline/fingerprint.py`:

```python
"""Version fingerprint for pipeline worker/API compatibility gating.

The fingerprint pins the clarinet package version plus a content hash of the
downstream ``plan/`` directory (``settings.config_tasks_path``). It is a startup
snapshot (``lru_cache``) — it reflects the code loaded into memory, not the files
currently on disk, so a ``git pull`` without a restart cannot fake a match.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from clarinet.settings import settings

# Non-source artifacts that differ between deploys without meaning a code change.
_SKIP_DIR_PARTS = {"__pycache__", ".git"}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".swp", ".tmp"}
_SKIP_NAMES = {".DS_Store"}


def clarinet_version() -> str:
    """Installed clarinet package version, or ``"unknown"`` if not installed."""
    try:
        return version("clarinet")
    except PackageNotFoundError:
        return "unknown"


def compute_plan_hash(root: Path) -> str:
    """Deterministic sha256 over all source files under *root*.

    Files are sorted by relative POSIX path; both the path and the content feed
    the hash (so renames change the result). Non-source artifacts are skipped.
    A missing root hashes as empty.
    """
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and not _SKIP_DIR_PARTS & set(p.parts)
        and p.suffix not in _SKIP_SUFFIXES
        and p.name not in _SKIP_NAMES
    )
    for p in files:
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


@lru_cache(maxsize=1)
def compute_fingerprint() -> str:
    """Full version fingerprint (startup snapshot, cached for process life)."""
    plan_hash = compute_plan_hash(Path(settings.config_tasks_path))
    return f"{clarinet_version()}:{plan_hash}"


@lru_cache(maxsize=1)
def queue_version_segment() -> str:
    """Short, queue-name-safe segment derived from the full fingerprint."""
    return hashlib.sha256(compute_fingerprint().encode()).hexdigest()[:12]


def reset_fingerprint_cache() -> None:
    """Clear cached fingerprint/segment — for tests that mutate config_tasks_path."""
    compute_fingerprint.cache_clear()
    queue_version_segment.cache_clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 uv run pytest tests/test_pipeline_fingerprint.py -v > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: PASS (6 passed).

- [ ] **Step 5: Add public exports + autouse cache reset**

In `clarinet/services/pipeline/__init__.py`, add to the imports/exports block:

```python
from clarinet.services.pipeline.fingerprint import (
    compute_fingerprint,
    queue_version_segment,
    reset_fingerprint_cache,
)
```

Add the same three names to the module's `__all__` if one exists.

In `tests/conftest.py`, add an autouse fixture (place near the `reset_brokers` fixture, ~line 40):

```python
@pytest.fixture(autouse=True)
def _reset_fingerprint_cache():
    from clarinet.services.pipeline.fingerprint import reset_fingerprint_cache

    reset_fingerprint_cache()
    yield
    reset_fingerprint_cache()
```

- [ ] **Step 6: Run to confirm nothing broke + commit**

Run: `timeout 120 uv run pytest tests/test_pipeline_fingerprint.py -q > /tmp/test-pipeline-version-gate.txt 2>&1; tail -20 /tmp/test-pipeline-version-gate.txt`
Expected: PASS.

```bash
git add clarinet/services/pipeline/fingerprint.py clarinet/services/pipeline/__init__.py tests/test_pipeline_fingerprint.py tests/conftest.py
git commit -m "feat(pipeline): add version fingerprint module"
```

---

### Task 2: Versioned queue names + settings flag

**Files:**
- Modify: `clarinet/settings.py` (flag near `:369`; `_versioned_queue` helper + 4 queue properties at `:445-468`)
- Modify: `tests/conftest.py` (set `pipeline_version_check_enabled=False` in the test settings fixture)
- Modify: `tests/test_pipeline_fingerprint.py` (queue-name tests)
- Modify: `.claude/rules/pipeline-ops.md` (settings table row)

**Interfaces:**
- Consumes: `queue_version_segment()` (Task 1).
- Produces: `settings.pipeline_version_check_enabled: bool`; `settings.default_queue_name`/`gpu_queue_name`/`dicom_queue_name`/`quarto_queue_name` now carry the segment when the flag is on.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_fingerprint.py`:

```python
from clarinet.settings import settings


def test_queue_name_versioned_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "config_tasks_path", str(tmp_path))
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", True)
    fp.reset_fingerprint_cache()
    seg = fp.queue_version_segment()
    ns = settings.pipeline_task_namespace
    assert settings.default_queue_name == f"{ns}.{seg}.default"
    assert settings.gpu_queue_name == f"{ns}.{seg}.gpu"


def test_queue_name_legacy_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", False)
    ns = settings.pipeline_task_namespace
    assert settings.default_queue_name == f"{ns}.default"


def test_dlq_never_versioned(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", True)
    fp.reset_fingerprint_cache()
    ns = settings.pipeline_task_namespace
    assert settings.dlq_queue_name == f"{ns}.dead_letter"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 uv run pytest tests/test_pipeline_fingerprint.py -k "queue_name or dlq" -v > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: FAIL — `AttributeError: ... pipeline_version_check_enabled` and/or default queue name lacks the segment.

- [ ] **Step 3: Add the flag**

In `clarinet/settings.py`, in the Pipeline settings block (after `pipeline_ack_type`, ~`:369`):

```python
    pipeline_version_check_enabled: bool = True  # Gate workers by version fingerprint (queue-name segment)
```

- [ ] **Step 4: Route queue-name properties through a versioned helper**

In `clarinet/settings.py`, replace the four task-queue properties (`:445-463`) and add the helper just above them. **Leave `dlq_queue_name` (`:465-468`) untouched.**

```python
    def _versioned_queue(self, kind: str) -> str:
        """Queue name with an optional version-fingerprint segment.

        With the gate enabled (default), embeds a short fingerprint of the
        clarinet version + ``plan/`` content so workers on stale code listen on
        different (dead) queues. Lazy import avoids a settings↔fingerprint cycle.
        """
        ns = self.pipeline_task_namespace
        if not self.pipeline_version_check_enabled:
            return f"{ns}.{kind}"
        from clarinet.services.pipeline.fingerprint import queue_version_segment

        return f"{ns}.{queue_version_segment()}.{kind}"

    @property
    def default_queue_name(self) -> str:
        """Project-namespaced default queue name (version-gated)."""
        return self._versioned_queue("default")

    @property
    def gpu_queue_name(self) -> str:
        """Project-namespaced GPU queue name (version-gated)."""
        return self._versioned_queue("gpu")

    @property
    def dicom_queue_name(self) -> str:
        """Project-namespaced DICOM queue name (version-gated)."""
        return self._versioned_queue("dicom")

    @property
    def quarto_queue_name(self) -> str:
        """Project-namespaced Quarto render queue name (version-gated)."""
        return self._versioned_queue("quarto")
```

- [ ] **Step 5: Keep the test suite green — disable the gate in test settings**

Find the test settings fixture in `tests/conftest.py` (the one that builds the `Settings`/overrides used by tests — search for `test_settings`). Set the gate off there so module-level `DEFAULT_QUEUE = settings.default_queue_name` (in `tests/test_pipeline.py:29`) and the existing `get_worker_queues` tests keep their legacy names:

```python
    # version gating off by default in tests; the fingerprint tests opt in explicitly
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", False)
```

(Adapt to how `test_settings` mutates settings — if it sets attributes on the `settings` singleton via `monkeypatch.setattr`, add the line there; if it constructs a `Settings(...)`, pass `pipeline_version_check_enabled=False`.)

- [ ] **Step 6: Run new + existing pipeline tests**

Run: `timeout 120 uv run pytest tests/test_pipeline_fingerprint.py tests/test_pipeline.py -q > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: PASS (new queue-name tests pass; existing `test_pipeline.py` still green).

- [ ] **Step 7: Document the setting + commit**

In `.claude/rules/pipeline-ops.md`, add a row to the Settings table:

```markdown
| `pipeline_version_check_enabled` (bool) | True | Embed a version fingerprint (clarinet version + `plan/` hash) into queue names so stale workers listen on dead queues; also enables the worker startup fingerprint diagnostic. Off = legacy un-versioned queue names |
```

```bash
git add clarinet/settings.py tests/conftest.py tests/test_pipeline_fingerprint.py .claude/rules/pipeline-ops.md
git commit -m "feat(pipeline): embed version fingerprint in queue names"
```

---

### Task 3: API fingerprint endpoint + lifespan warm-up

**Files:**
- Modify: `clarinet/api/routers/pipeline.py` (new `GET /fingerprint`)
- Modify: `clarinet/api/app.py` (warm the snapshot in lifespan, after `:246`)
- Modify: `tests/utils/urls.py` (URL constant, after `:122`)
- Modify: `tests/test_pipeline_task_run.py` (endpoint test — reuses that file's API client fixtures)

**Interfaces:**
- Consumes: `compute_fingerprint()` (Task 1).
- Produces: `GET /api/pipelines/fingerprint` → `{"fingerprint": "<str>"}` (no auth); `PIPELINE_FINGERPRINT` URL constant.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline_task_run.py` (uses the existing authenticated `client` fixture from `tests/conftest.py`):

```python
async def test_fingerprint_endpoint(client) -> None:
    from clarinet.services.pipeline.fingerprint import (
        compute_fingerprint,
        reset_fingerprint_cache,
    )
    from tests.utils.urls import PIPELINE_FINGERPRINT

    reset_fingerprint_cache()
    resp = await client.get(PIPELINE_FINGERPRINT)
    assert resp.status_code == 200
    assert resp.json()["fingerprint"] == compute_fingerprint()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 uv run pytest tests/test_pipeline_task_run.py::test_fingerprint_endpoint -v > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: FAIL — `ImportError: cannot import name 'PIPELINE_FINGERPRINT'` (or 404 once the const exists).

- [ ] **Step 3: Add the URL constant**

In `tests/utils/urls.py`, after `PIPELINES_BASE = "/api/pipelines"` (`:122`):

```python
PIPELINE_FINGERPRINT = "/api/pipelines/fingerprint"
```

- [ ] **Step 4: Add the endpoint**

In `clarinet/api/routers/pipeline.py`, add (place above `get_pipeline_definition`, so the static `/fingerprint` path is matched before the `/{name}/definition` catch-all):

```python
@router.get("/fingerprint")
async def get_fingerprint() -> dict[str, str]:
    """Return the running API's version fingerprint (no auth — worker-facing).

    Workers compare this against their own fingerprint at startup to detect that
    they are running stale code (and are therefore listening on dead queues).
    """
    from clarinet.services.pipeline.fingerprint import compute_fingerprint

    return {"fingerprint": compute_fingerprint()}
```

- [ ] **Step 5: Warm the snapshot in lifespan**

In `clarinet/api/app.py`, immediately after `_ensure_record_types_imported()` (`:246`):

```python
        from clarinet.services.pipeline.fingerprint import compute_fingerprint

        compute_fingerprint()  # pin the startup snapshot before any later plan/ edits
```

- [ ] **Step 6: Run test to verify it passes**

Run: `timeout 120 uv run pytest tests/test_pipeline_task_run.py::test_fingerprint_endpoint -v > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add clarinet/api/routers/pipeline.py clarinet/api/app.py tests/utils/urls.py tests/test_pipeline_task_run.py
git commit -m "feat(pipeline): expose API version fingerprint endpoint"
```

---

### Task 4: ClarinetClient.get_worker_fingerprint

**Files:**
- Modify: `clarinet/client.py` (new method after `get_me`, ~`:368`)
- Modify: `tests/test_client.py` (method test — mock `_request`)

**Interfaces:**
- Consumes: `ClarinetClient._request` (`client.py:179`).
- Produces: `ClarinetClient.get_worker_fingerprint() -> str`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_client.py`:

```python
import httpx

from clarinet.client import ClarinetClient


async def test_get_worker_fingerprint(monkeypatch) -> None:
    client = ClarinetClient("http://x/api", service_token="t", auto_login=False)

    async def fake_request(method, endpoint, **kwargs):
        assert (method, endpoint) == ("GET", "/pipelines/fingerprint")
        return httpx.Response(200, json={"fingerprint": "1.0:abc"})

    monkeypatch.setattr(client, "_request", fake_request)
    try:
        assert await client.get_worker_fingerprint() == "1.0:abc"
    finally:
        await client.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 uv run pytest tests/test_client.py::test_get_worker_fingerprint -v > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: FAIL — `AttributeError: 'ClarinetClient' object has no attribute 'get_worker_fingerprint'`.

- [ ] **Step 3: Add the method**

In `clarinet/client.py`, after `get_me` (`:368`):

```python
    async def get_worker_fingerprint(self) -> str:
        """Fetch the running API's version fingerprint.

        Returns the ``{clarinet_version}:{plan_hash}`` string. Raises
        ``ClarinetAPIError`` on transport/HTTP errors, including a 404 from an
        API too old to expose the endpoint.
        """
        response = await self._request("GET", "/pipelines/fingerprint")
        return response.json()["fingerprint"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 uv run pytest tests/test_client.py::test_get_worker_fingerprint -v > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clarinet/client.py tests/test_client.py
git commit -m "feat(client): add get_worker_fingerprint"
```

---

### Task 5: Worker startup diagnostic

**Files:**
- Modify: `clarinet/services/pipeline/worker.py` (`warn_if_stale` helper + call in `run_worker`)
- Modify: `tests/test_pipeline.py` (diagnostic tests)

**Interfaces:**
- Consumes: `compute_fingerprint()` (Task 1), `ClarinetClient.get_worker_fingerprint()` (Task 4), `settings.pipeline_version_check_enabled`, `settings.effective_api_base_url`, `settings.effective_service_token`.
- Produces: `async warn_if_stale(queues: list[str]) -> None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
from unittest.mock import AsyncMock, patch

from clarinet.services.pipeline.worker import warn_if_stale


async def test_warn_if_stale_logs_error_on_mismatch(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", True)
    monkeypatch.setattr(
        "clarinet.services.pipeline.fingerprint.compute_fingerprint", lambda: "mine"
    )
    mock_client = AsyncMock()
    mock_client.get_worker_fingerprint = AsyncMock(return_value="theirs")
    mock_client.close = AsyncMock()
    with patch("clarinet.client.ClarinetClient", return_value=mock_client):
        with caplog.at_level("ERROR"):
            await warn_if_stale(["proj.default"])
    assert "stale" in caplog.text.lower()
    mock_client.close.assert_awaited_once()


async def test_warn_if_stale_info_on_match(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", True)
    monkeypatch.setattr(
        "clarinet.services.pipeline.fingerprint.compute_fingerprint", lambda: "same"
    )
    mock_client = AsyncMock()
    mock_client.get_worker_fingerprint = AsyncMock(return_value="same")
    mock_client.close = AsyncMock()
    with patch("clarinet.client.ClarinetClient", return_value=mock_client):
        with caplog.at_level("INFO"):
            await warn_if_stale(["proj.default"])
    assert "matches" in caplog.text.lower()


async def test_warn_if_stale_disabled_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", False)
    with patch("clarinet.client.ClarinetClient") as mk:
        await warn_if_stale(["proj.default"])
    mk.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 uv run pytest tests/test_pipeline.py -k warn_if_stale -v > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: FAIL — `ImportError: cannot import name 'warn_if_stale'`.

- [ ] **Step 3: Add the helper**

In `clarinet/services/pipeline/worker.py`, add (top-level, after `get_worker_queues`):

```python
async def warn_if_stale(queues: list[str]) -> None:
    """Log a loud ERROR if our fingerprint differs from the running API's.

    Diagnostic only — broker-level isolation (version-gated queue names) already
    prevents a stale worker from receiving tasks. Never raises: a 404 (old API
    without the endpoint) or an unreachable API downgrades to a WARNING.
    """
    if not settings.pipeline_version_check_enabled:
        return

    from clarinet.client import ClarinetAPIError, ClarinetClient
    from clarinet.services.pipeline.fingerprint import compute_fingerprint

    client = ClarinetClient(
        base_url=settings.effective_api_base_url,
        service_token=settings.effective_service_token,
    )
    try:
        api_fp = await client.get_worker_fingerprint()
        mine = compute_fingerprint()
        if api_fp != mine:
            logger.error(
                f"Worker fingerprint {mine} != API {api_fp}; listening on stale "
                f"queues {queues} — will NOT receive new tasks until the worker's "
                f"code is updated and the process restarted."
            )
        else:
            logger.info(f"Worker fingerprint matches API: {mine}")
    except ClarinetAPIError as e:
        # _request wraps transport/HTTP errors (incl. 404 from an old API) here.
        logger.warning(f"Could not verify worker fingerprint against API: {e}")
    except Exception as e:  # diagnostic must never crash worker startup
        logger.warning(f"Unexpected error verifying worker fingerprint: {e}")
    finally:
        await client.close()
```

- [ ] **Step 4: Call it from `run_worker`**

In `clarinet/services/pipeline/worker.py`, in `run_worker`, right after the
`logger.info(f"Starting pipeline worker on queues: ...")` line (`:197`) and
before `brokers = [get_broker_for(q) for q in queues]` (`:199`):

```python
        await warn_if_stale(queues)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `timeout 120 uv run pytest tests/test_pipeline.py -k warn_if_stale -v > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add clarinet/services/pipeline/worker.py tests/test_pipeline.py
git commit -m "feat(pipeline): warn at worker startup on version mismatch"
```

---

## Follow-up (separate plan): stale-queue reclaim

Not part of this plan — the gate works without it. Empty old-segment queues
(`{ns}.<old-seg>.{default,gpu,dicom,quarto}`) accumulate after upgrades; they
self-clean only if `x-expires` is added to task queues, otherwise an operator
clears them. A future plan can extend `uv run clarinet rabbitmq clean`
(`clarinet/services/pipeline/rabbitmq_cleanup.py` + the CLI wiring in
`clarinet/cli/main.py`) to delete consumer-less, empty versioned queues whose
segment ≠ `queue_version_segment()`, never touching the DLQ or the current
segment. That plan must be written after reading `rabbitmq_cleanup.py` so its
steps carry real code (the Management-HTTP-API enumerate/delete helpers).

---

## Final verification

- [ ] Full pipeline suite: `timeout 180 uv run pytest tests/test_pipeline.py tests/test_pipeline_fingerprint.py tests/test_pipeline_task_run.py tests/test_client.py -q > /tmp/test-pipeline-version-gate.txt 2>&1; tail -30 /tmp/test-pipeline-version-gate.txt` → all green.
- [ ] `make check` (format + lint + typecheck) passes. After it, **re-Read any file before further edits** (`ruff format` may have rewritten it).
- [ ] New untyped imports? None expected (`importlib.metadata`, `hashlib` are stdlib).
- [ ] Run `pr-diff-reviewer` before the first `gh pr create`.

## Notes for the implementer

- The gate's effect only kicks in once **both** API and workers run a version with this feature; pre-feature workers listen on un-versioned names and stop receiving immediately, but their leftover tasks in `{ns}.default` need draining by an old worker during the first deploy (spec "Bootstrap limit").
- Tasks published to an old-segment queue right before a full upgrade orphan there; Task 6 (or `x-expires`) reclaims the empty queues afterwards.
