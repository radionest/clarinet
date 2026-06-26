# Admin per-user workload statistics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Workload by user" section to the admin dashboard — a table of assigned-record counts per active user (inwork / pending / blocked / failed) plus a global "available pending" count.

**Architecture:** Extend the existing `GET /api/admin/stats` response (no new endpoint) so the table auto-refreshes through the dashboard's existing post-mutation re-fetch. Two new read-only aggregation queries in `RecordRepository`, surfaced by `AdminService.get_stats()` into new `AdminStats` fields, then decoded and rendered by the Gleam frontend.

**Tech Stack:** Python 3 (FastAPI, SQLModel, async SQLAlchemy), pytest + pytest-asyncio; Gleam + Lustre frontend, gleeunit.

**Spec:** `docs/superpowers/specs/2026-06-26-admin-workload-stats-design.md`

## Global Constraints

- **Semantics (copy verbatim):** "assigned" = `Record.user_id IS NOT NULL`; "available pending" = `status == pending AND user_id IS NULL`; surfaced statuses are exactly `inwork`, `pending`, `blocked`, `failed`. Rows = **active** users (`is_active = True`), including zero-count rows; deactivated users excluded.
- **No DB migration** — only additive response fields and new read queries.
- **All Python commands run through `uv run`.** The **first** `uv`/`pytest`/`make` command in this fresh worktree builds the venv — wrap it with `timeout 300`; later runs use `timeout 120`. Redirect test output to a unique file (`> /tmp/test-admin-workload.txt 2>&1`), **never pipe** (`| tail`, `| tee`).
- **SQLAlchemy NULL checks:** use `col(Record.user_id).is_(None)` / `.is_not(None)` — never `== None`.
- **Shared `AsyncSession`:** sequential `await` only; no `asyncio.gather` across queries.
- **Frontend:** run `gleam test` and `gleam check` from `clarinet/frontend/` (the gleam binary may be at `/home/linuxbrew/.linuxbrew/bin/gleam`); build via `make frontend-build` from the repo root.
- **Commits:** Conventional Commits, English, **no** `Co-Authored-By` trailer.

---

### Task 1: Repository aggregation methods

**Files:**
- Modify: `clarinet/repositories/record_repository.py` (add two methods after `get_per_type_unique_users`, ~line 1613)
- Test: `tests/integration/test_repositories.py` (add to `class TestRecordRepository`, after `test_get_per_type_status_counts`, ~line 803)

**Interfaces:**
- Consumes: existing `Record` model, `RecordStatus`, `func`, `col` — all already imported in `record_repository.py`.
- Produces:
  - `RecordRepository.get_assigned_status_counts_by_user() -> dict[str, dict[str, int]]` — `{str(user_id): {status_value: count}}`, only `user_id IS NOT NULL`.
  - `RecordRepository.count_unassigned_pending() -> int` — count of `pending` records with `user_id IS NULL`.

The `TestRecordRepository.env` fixture already seeds one record assigned to `env["user"]` with the default `pending` status; the tests below build on that.

- [ ] **Step 1: Write the failing tests**

Add to `tests/integration/test_repositories.py` inside `class TestRecordRepository` (after `test_get_per_type_status_counts`). All factory helpers (`seed_record`) and `RecordStatus` are already imported at the top of the file.

```python
    @pytest.mark.asyncio
    async def test_get_assigned_status_counts_by_user(self, env):
        session = env["session"]
        user = env["user"]  # env already seeded 1 assigned pending record for this user
        # second assigned record for the same user, status inwork
        await seed_record(
            session,
            patient_id="RPAT",
            study_uid="1.2.3.300",
            series_uid="1.2.3.300.1",
            rt_name="rec-rt-00001",
            user_id=user.id,
            status=RecordStatus.inwork,
        )
        # unassigned pending record — must NOT appear in the per-user map
        await seed_record(
            session,
            patient_id="RPAT",
            study_uid="1.2.3.300",
            series_uid="1.2.3.300.1",
            rt_name="rec-rt-00001",
            status=RecordStatus.pending,
        )

        counts = await env["repo"].get_assigned_status_counts_by_user()

        assert counts[str(user.id)]["pending"] == 1
        assert counts[str(user.id)]["inwork"] == 1
        # the unassigned record's NULL user_id is never a key
        assert "None" not in counts
        assert None not in counts

    @pytest.mark.asyncio
    async def test_count_unassigned_pending(self, env):
        session = env["session"]
        # env's record is assigned → not counted; add one unassigned pending (counted)
        await seed_record(
            session,
            patient_id="RPAT",
            study_uid="1.2.3.300",
            series_uid="1.2.3.300.1",
            rt_name="rec-rt-00001",
            status=RecordStatus.pending,
        )
        # unassigned but inwork → not counted
        await seed_record(
            session,
            patient_id="RPAT",
            study_uid="1.2.3.300",
            series_uid="1.2.3.300.1",
            rt_name="rec-rt-00001",
            status=RecordStatus.inwork,
        )

        assert await env["repo"].count_unassigned_pending() == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (first command in the worktree — builds venv):
```bash
timeout 300 uv run pytest "tests/integration/test_repositories.py::TestRecordRepository::test_get_assigned_status_counts_by_user" "tests/integration/test_repositories.py::TestRecordRepository::test_count_unassigned_pending" -v > /tmp/test-admin-workload.txt 2>&1; tail -40 /tmp/test-admin-workload.txt
```
Expected: FAIL — `AttributeError: 'RecordRepository' object has no attribute 'get_assigned_status_counts_by_user'`.

- [ ] **Step 3: Implement the two methods**

In `clarinet/repositories/record_repository.py`, immediately after the `get_per_type_unique_users` method (ends ~line 1613), add:

```python
    async def get_assigned_status_counts_by_user(self) -> dict[str, dict[str, int]]:
        """Per assigned user, per status: ``{str(user_id): {status_value: count}}``.

        Only records with a user assigned (``user_id IS NOT NULL``) are counted.
        All statuses are returned; the caller selects the ones it surfaces.
        """
        query = (
            select(col(Record.user_id), col(Record.status), func.count())
            .where(col(Record.user_id).is_not(None))
            .group_by(col(Record.user_id), col(Record.status))
        )
        result = await self.session.execute(query)
        out: dict[str, dict[str, int]] = {}
        for user_id, status, count in result.all():
            out.setdefault(str(user_id), {})[status.value] = count
        return out

    async def count_unassigned_pending(self) -> int:
        """Count of ``pending`` records with no user assigned (the claimable pool)."""
        query = select(func.count()).where(
            col(Record.user_id).is_(None),
            col(Record.status) == RecordStatus.pending,
        )
        result = await self.session.execute(query)
        return result.scalar() or 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
timeout 120 uv run pytest "tests/integration/test_repositories.py::TestRecordRepository::test_get_assigned_status_counts_by_user" "tests/integration/test_repositories.py::TestRecordRepository::test_count_unassigned_pending" -v > /tmp/test-admin-workload.txt 2>&1; tail -40 /tmp/test-admin-workload.txt
```
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add clarinet/repositories/record_repository.py tests/integration/test_repositories.py
git commit -m "feat(repositories): per-user assigned status counts + unassigned pending count"
```

---

### Task 2: Admin models + service

**Files:**
- Modify: `clarinet/models/admin.py` (add `UserWorkload`; extend `AdminStats`)
- Modify: `clarinet/services/admin_service.py` (import `UserWorkload`; extend `get_stats`)
- Test: `tests/test_services.py` (add to `class TestAdminService`; extend imports)
- Test: `tests/integration/test_admin_record_management.py` (add endpoint test; extend imports)

**Interfaces:**
- Consumes: `RecordRepository.get_assigned_status_counts_by_user()`, `RecordRepository.count_unassigned_pending()` (Task 1); `self.user_repo.list_all(is_active=True)` (base-repo field filter, returns `Sequence[User]`).
- Produces:
  - `clarinet.models.admin.UserWorkload(user_id: str, email: str, inwork: int, pending: int, blocked: int, failed: int)` (status fields default `0`).
  - `AdminStats.available_pending: int = 0`, `AdminStats.workload_by_user: list[UserWorkload] = []`.
  - JSON contract on `GET /api/admin/stats`: `available_pending` (int) and `workload_by_user` (list of `{user_id, email, inwork, pending, blocked, failed}`), one entry per active user, sorted by email.

- [ ] **Step 1: Write the failing service test**

In `tests/test_services.py`, first extend the factory import (currently `from tests.utils.factories import make_patient`) to:

```python
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    make_user,
    seed_record,
)
```

Then add to `class TestAdminService` (after `test_get_record_type_stats_with_data`):

```python
    @pytest.mark.asyncio
    async def test_get_stats_workload_by_user(self, env):
        session = env["session"]
        pat = make_patient("WL_PAT", "Workload Patient")
        session.add(pat)
        await session.commit()
        study = make_study("WL_PAT", "1.2.3.700")
        session.add(study)
        await session.commit()
        series = make_series("1.2.3.700", "1.2.3.700.1", 1)
        session.add(series)
        await session.commit()
        rt = make_record_type("wl-rt")
        session.add(rt)
        await session.commit()

        u_busy = make_user(email="wl_busy@test.com", is_active=True)
        u_idle = make_user(email="wl_idle@test.com", is_active=True)
        u_off = make_user(email="wl_off@test.com", is_active=False)
        session.add_all([u_busy, u_idle, u_off])
        await session.commit()
        for u in (u_busy, u_idle, u_off):
            await session.refresh(u)

        async def add(user_id, status):
            await seed_record(
                session,
                patient_id="WL_PAT",
                study_uid="1.2.3.700",
                series_uid="1.2.3.700.1",
                rt_name="wl-rt",
                user_id=user_id,
                status=status,
            )

        await add(u_busy.id, RecordStatus.inwork)
        await add(u_busy.id, RecordStatus.failed)
        await add(u_off.id, RecordStatus.inwork)  # inactive user → excluded
        await add(None, RecordStatus.pending)  # available pool
        await add(None, RecordStatus.pending)  # available pool

        stats = await env["service"].get_stats()

        assert stats.available_pending == 2
        by_email = {w.email: w for w in stats.workload_by_user}
        assert "wl_busy@test.com" in by_email
        assert "wl_idle@test.com" in by_email  # active, zero-count row present
        assert "wl_off@test.com" not in by_email  # deactivated excluded
        assert by_email["wl_busy@test.com"].inwork == 1
        assert by_email["wl_busy@test.com"].failed == 1
        assert by_email["wl_busy@test.com"].pending == 0
        assert by_email["wl_idle@test.com"].inwork == 0
```

- [ ] **Step 2: Run the service test to verify it fails**

Run:
```bash
timeout 120 uv run pytest "tests/test_services.py::TestAdminService::test_get_stats_workload_by_user" -v > /tmp/test-admin-workload.txt 2>&1; tail -40 /tmp/test-admin-workload.txt
```
Expected: FAIL — `AttributeError: 'AdminStats' object has no attribute 'available_pending'` (or a `TypeError` on the missing constructor kwargs).

- [ ] **Step 3: Add the models**

In `clarinet/models/admin.py`, add the `UserWorkload` class (place it just above `AdminStats`) and two fields on `AdminStats`:

```python
class UserWorkload(PydanticBaseModel):
    """Assigned-record counts for one user, by active-workload status."""

    user_id: str
    email: str
    inwork: int = 0
    pending: int = 0
    blocked: int = 0
    failed: int = 0


class AdminStats(PydanticBaseModel):
    """Aggregate system statistics for admin dashboard."""

    total_studies: int
    total_records: int
    total_users: int
    total_patients: int
    records_by_status: dict[str, int]
    available_pending: int = 0
    workload_by_user: list[UserWorkload] = []
```

- [ ] **Step 4: Extend the service**

In `clarinet/services/admin_service.py`, add `UserWorkload` to the `clarinet.models.admin` import block:

```python
from clarinet.models.admin import (
    AdminStats,
    RecordTypeStats,
    RecordTypeStatusCounts,
    RoleMatrixResponse,
    UserRoleInfo,
    UserWorkload,
)
```

Then replace the body of `get_stats` (currently lines ~56-64) so it also computes the workload. The method becomes:

```python
        total_studies, total_records, total_users, total_patients = await self._get_total_counts()
        records_by_status = await self._get_records_by_status()
        assigned = await self.record_repo.get_assigned_status_counts_by_user()
        available_pending = await self.record_repo.count_unassigned_pending()
        active_users = await self.user_repo.list_all(is_active=True)
        workload_by_user = [
            UserWorkload(
                user_id=str(user.id),
                email=user.email,
                inwork=assigned.get(str(user.id), {}).get("inwork", 0),
                pending=assigned.get(str(user.id), {}).get("pending", 0),
                blocked=assigned.get(str(user.id), {}).get("blocked", 0),
                failed=assigned.get(str(user.id), {}).get("failed", 0),
            )
            for user in sorted(active_users, key=lambda u: u.email)
        ]
        return AdminStats(
            total_studies=total_studies,
            total_records=total_records,
            total_users=total_users,
            total_patients=total_patients,
            records_by_status=records_by_status,
            available_pending=available_pending,
            workload_by_user=workload_by_user,
        )
```

(Queries stay sequential — shared `AsyncSession`. Leave the existing method docstring in place.)

- [ ] **Step 5: Run the service test to verify it passes**

Run:
```bash
timeout 120 uv run pytest "tests/test_services.py::TestAdminService::test_get_stats_workload_by_user" -v > /tmp/test-admin-workload.txt 2>&1; tail -40 /tmp/test-admin-workload.txt
```
Expected: PASS.

- [ ] **Step 6: Write the failing endpoint test**

In `tests/integration/test_admin_record_management.py`, extend the imports — add `RecordStatus` and `ADMIN_STATS`:

```python
from clarinet.models.base import RecordStatus
from tests.utils.urls import ADMIN_RECORD_STATUS, ADMIN_RECORD_USER, ADMIN_STATS
```

Then add a new test class at the end of the file:

```python
class TestAdminStatsWorkload:
    """GET /api/admin/stats carries per-user workload + available pending."""

    @pytest.mark.asyncio
    async def test_admin_stats_includes_workload(self, client, record_env, test_session):
        user = record_env["user"]  # env seeded 1 assigned pending record for this user
        # one unassigned pending record → the available pool
        await seed_record(
            test_session,
            patient_id="ADMIN_PAT",
            study_uid="1.2.3.900",
            series_uid="1.2.3.900.1",
            rt_name="admin-test-rt",
            status=RecordStatus.pending,
        )

        resp = await client.get(ADMIN_STATS)
        assert resp.status_code == 200
        data = resp.json()

        assert data["available_pending"] == 1
        by_email = {w["email"]: w for w in data["workload_by_user"]}
        assert user.email in by_email
        assert by_email[user.email]["pending"] == 1
```

- [ ] **Step 7: Run the endpoint test to verify it passes**

The implementation from Steps 3-4 already satisfies it; this confirms the wire format.
```bash
timeout 120 uv run pytest "tests/integration/test_admin_record_management.py::TestAdminStatsWorkload" -v > /tmp/test-admin-workload.txt 2>&1; tail -40 /tmp/test-admin-workload.txt
```
Expected: PASS.

- [ ] **Step 8: Type-check and commit**

```bash
timeout 120 uv run mypy clarinet/models/admin.py clarinet/services/admin_service.py clarinet/repositories/record_repository.py > /tmp/test-admin-workload.txt 2>&1; tail -20 /tmp/test-admin-workload.txt
```
Expected: no new errors. Then:
```bash
git add clarinet/models/admin.py clarinet/services/admin_service.py tests/test_services.py tests/integration/test_admin_record_management.py
git commit -m "feat(admin): per-user workload + available-pending in admin stats"
```

---

### Task 3: Frontend models + decoder

**Files:**
- Modify: `clarinet/frontend/src/api/models.gleam` (add `UserWorkload`; extend `AdminStats`)
- Modify: `clarinet/frontend/src/api/admin.gleam` (add `user_workload_decoder`; extend `admin_stats_decoder`)
- Test: `clarinet/frontend/test/admin_stats_test.gleam` (new)

**Interfaces:**
- Consumes: the JSON contract from Task 2.
- Produces:
  - `models.UserWorkload(user_id: String, email: String, inwork: Int, pending: Int, blocked: Int, failed: Int)`.
  - `models.AdminStats` extended with `available_pending: Int`, `workload_by_user: List(UserWorkload)`.
  - `admin_api.admin_stats_decoder()` (already `pub`) now also decodes the two new fields.

- [ ] **Step 1: Write the failing decoder test**

Create `clarinet/frontend/test/admin_stats_test.gleam`:

```gleam
import api/admin as admin_api
import gleam/dict
import gleam/json
import gleam/list
import gleeunit
import gleeunit/should

pub fn main() {
  gleeunit.main()
}

pub fn admin_stats_decoder_workload_test() {
  let json_str =
    "{\"total_studies\":2,\"total_records\":5,\"total_users\":3,"
    <> "\"total_patients\":1,\"records_by_status\":{\"pending\":4,\"inwork\":1},"
    <> "\"available_pending\":3,"
    <> "\"workload_by_user\":["
    <> "{\"user_id\":\"u-1\",\"email\":\"a@x.org\",\"inwork\":1,\"pending\":2,"
    <> "\"blocked\":0,\"failed\":1}]}"

  let assert Ok(stats) = json.parse(json_str, admin_api.admin_stats_decoder())

  should.equal(stats.available_pending, 3)
  should.equal(list.length(stats.workload_by_user), 1)
  let assert [w] = stats.workload_by_user
  should.equal(w.user_id, "u-1")
  should.equal(w.email, "a@x.org")
  should.equal(w.inwork, 1)
  should.equal(w.pending, 2)
  should.equal(w.blocked, 0)
  should.equal(w.failed, 1)
  should.equal(dict.size(stats.records_by_status), 2)
}
```

- [ ] **Step 2: Run the frontend test to verify it fails**

Run from `clarinet/frontend/`:
```bash
gleam test
```
Expected: compile error — `AdminStats` has no field `available_pending` / `workload_by_user` (and `admin_stats_decoder` shape mismatch).

- [ ] **Step 3: Extend the Gleam models**

In `clarinet/frontend/src/api/models.gleam`, replace the `AdminStats` type (currently lines ~372-380) and add `UserWorkload` just above it:

```gleam
// Per-user assigned-record workload counts (admin dashboard)
pub type UserWorkload {
  UserWorkload(
    user_id: String,
    email: String,
    inwork: Int,
    pending: Int,
    blocked: Int,
    failed: Int,
  )
}

pub type AdminStats {
  AdminStats(
    total_studies: Int,
    total_records: Int,
    total_users: Int,
    total_patients: Int,
    records_by_status: Dict(String, Int),
    available_pending: Int,
    workload_by_user: List(UserWorkload),
  )
}
```

- [ ] **Step 4: Extend the decoder**

In `clarinet/frontend/src/api/admin.gleam`, add a `user_workload_decoder` (place it just above `admin_stats_decoder`):

```gleam
fn user_workload_decoder() -> decode.Decoder(models.UserWorkload) {
  use user_id <- decode.field("user_id", decode.string)
  use email <- decode.field("email", decode.string)
  use inwork <- decode.field("inwork", decode.int)
  use pending <- decode.field("pending", decode.int)
  use blocked <- decode.field("blocked", decode.int)
  use failed <- decode.field("failed", decode.int)

  decode.success(models.UserWorkload(
    user_id: user_id,
    email: email,
    inwork: inwork,
    pending: pending,
    blocked: blocked,
    failed: failed,
  ))
}
```

Then extend `admin_stats_decoder` — add the two new `decode.field` lines before `decode.success` and the two new constructor fields:

```gleam
pub fn admin_stats_decoder() -> decode.Decoder(AdminStats) {
  use total_studies <- decode.field("total_studies", decode.int)
  use total_records <- decode.field("total_records", decode.int)
  use total_users <- decode.field("total_users", decode.int)
  use total_patients <- decode.field("total_patients", decode.int)
  use records_by_status <- decode.field(
    "records_by_status",
    decode.dict(decode.string, decode.int),
  )
  use available_pending <- decode.field("available_pending", decode.int)
  use workload_by_user <- decode.field(
    "workload_by_user",
    decode.list(user_workload_decoder()),
  )

  decode.success(models.AdminStats(
    total_studies: total_studies,
    total_records: total_records,
    total_users: total_users,
    total_patients: total_patients,
    records_by_status: records_by_status,
    available_pending: available_pending,
    workload_by_user: workload_by_user,
  ))
}
```

- [ ] **Step 5: Run the frontend test to verify it passes**

Run from `clarinet/frontend/`:
```bash
gleam test
```
Expected: PASS (all gleeunit tests green, including `admin_stats_decoder_workload_test`).

- [ ] **Step 6: Commit**

```bash
git add clarinet/frontend/src/api/models.gleam clarinet/frontend/src/api/admin.gleam clarinet/frontend/test/admin_stats_test.gleam
git commit -m "feat(frontend): decode workload_by_user / available_pending in AdminStats"
```

---

### Task 4: Frontend dashboard view

**Files:**
- Modify: `clarinet/frontend/src/pages/admin.gleam` (add `workload_section` + helpers; wire into `stats_view`)

**Interfaces:**
- Consumes: `models.AdminStats.available_pending`, `models.AdminStats.workload_by_user`, `models.UserWorkload` (Task 3); existing helpers `admin_stat_card(label:, count:, color:, route:)`, `router.Records(Dict(String,String))`, `router.route_to_href`, `record_filters.unassigned_user_value`.
- Produces: a rendered "Workload by user" section between "Records by Status" and "Role Matrix".

Note: `dict`, `list`, `int`, `router`, `record_filters`, `attribute`, `html`, `element` are all already imported in `admin.gleam`. This task has no unit test of its own (it is pure view composition over the Task 3 types); it is verified by a successful type-check and production build.

- [ ] **Step 1: Add the section + helpers**

In `clarinet/frontend/src/pages/admin.gleam`, add these three functions next to `status_section` (e.g. right after it, ~line 496):

```gleam
fn workload_section(stats: models.AdminStats) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Workload by user")]),
    html.div([attribute.class("stats-grid")], [
      admin_stat_card(
        label: "Available pending",
        count: stats.available_pending,
        color: "blue",
        route: Some(
          router.Records(
            dict.from_list([
              #("status", "pending"),
              #("user", record_filters.unassigned_user_value),
            ]),
          ),
        ),
      ),
    ]),
    html.div([attribute.class("table-responsive")], [
      html.table([attribute.class("table")], [
        html.thead([], [
          html.tr([], [
            html.th([], [html.text("User")]),
            html.th([], [html.text("inwork")]),
            html.th([], [html.text("pending")]),
            html.th([], [html.text("blocked")]),
            html.th([], [html.text("failed")]),
            html.th([], [html.text("Total")]),
          ]),
        ]),
        html.tbody([], list.map(stats.workload_by_user, workload_row)),
      ]),
    ]),
  ])
}

fn workload_row(w: models.UserWorkload) -> Element(Msg) {
  let total = w.inwork + w.pending + w.blocked + w.failed
  html.tr([], [
    html.td([], [html.text(w.email)]),
    workload_cell(w.user_id, "inwork", w.inwork),
    workload_cell(w.user_id, "pending", w.pending),
    workload_cell(w.user_id, "blocked", w.blocked),
    workload_cell(w.user_id, "failed", w.failed),
    html.td([], [html.text(int.to_string(total))]),
  ])
}

fn workload_cell(user_id: String, status_str: String, count: Int) -> Element(Msg) {
  case count {
    0 -> html.td([attribute.class("text-muted")], [html.text("0")])
    _ ->
      html.td([], [
        html.a(
          [
            attribute.href(
              router.route_to_href(
                router.Records(
                  dict.from_list([
                    #("status", status_str),
                    #("user", user_id),
                  ]),
                ),
              ),
            ),
          ],
          [html.text(int.to_string(count))],
        ),
      ])
  }
}
```

- [ ] **Step 2: Wire the section into `stats_view`**

In `clarinet/frontend/src/pages/admin.gleam`, in `stats_view`, find the `Some(stats)` branch (currently ~line 423):

```gleam
        Some(stats) ->
          element.fragment([overview_section(stats), status_section(stats)])
```

and replace it with:

```gleam
        Some(stats) ->
          element.fragment([
            overview_section(stats),
            status_section(stats),
            workload_section(stats),
          ])
```

- [ ] **Step 3: Type-check the frontend**

Run from the repo root:
```bash
make frontend-check > /tmp/test-admin-workload.txt 2>&1; tail -30 /tmp/test-admin-workload.txt
```
Expected: no errors (`gleam check` clean).

- [ ] **Step 4: Production build**

Run from the repo root:
```bash
make frontend-build > /tmp/test-admin-workload.txt 2>&1; tail -30 /tmp/test-admin-workload.txt
```
Expected: build succeeds; `clarinet/static/clarinet_frontend.js` is regenerated.

- [ ] **Step 5: Commit**

```bash
git add clarinet/frontend/src/pages/admin.gleam clarinet/static/clarinet_frontend.js
git commit -m "feat(frontend): admin dashboard workload-by-user table"
```

---

## Final verification (after all tasks)

- [ ] Run the affected backend tests together:
```bash
timeout 180 uv run pytest tests/test_services.py tests/integration/test_repositories.py tests/integration/test_admin_record_management.py -q > /tmp/test-admin-workload.txt 2>&1; tail -30 /tmp/test-admin-workload.txt
```
Expected: all pass.
- [ ] `make check` (format + lint + typecheck) clean for the touched Python files.
- [ ] Frontend `gleam test` green and `make frontend-build` succeeds.
- [ ] Before the first `gh pr create`: run `Agent(subagent_type=pr-diff-reviewer)` (per project CLAUDE.md — not `SKIP_PR_REVIEW`).

## Self-review (performed during planning)

- **Spec coverage:** per-user table → Tasks 2-4; active-users-only rows incl. zeros → Task 2 service + test; inactive-user exclusion → Task 2 service test; four status columns + Total → Task 4; available-pending number → Tasks 1/2/4; extend `/admin/stats` (no new endpoint, auto-refresh) → Task 2; clickable cells → Task 4; no migration → respected (read-only queries + additive fields). All spec sections map to a task.
- **Placeholder scan:** none — every code/test/command step is concrete.
- **Type consistency:** `get_assigned_status_counts_by_user` / `count_unassigned_pending` names match across Tasks 1-2; `UserWorkload` field set (`user_id, email, inwork, pending, blocked, failed`) identical in backend (Task 2) and frontend (Task 3) and consumed unchanged in Task 4; `available_pending` / `workload_by_user` names consistent backend↔frontend.
