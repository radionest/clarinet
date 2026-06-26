# Admin dashboard — per-user workload statistics

**Date:** 2026-06-26
**Status:** Approved design, ready for implementation planning
**Area:** `clarinet/api/admin`, `clarinet/services/admin_service.py`, `clarinet/repositories/record_repository.py`, `clarinet/frontend` (admin dashboard)

## Context

The admin dashboard (`/admin`, `clarinet/frontend/src/pages/admin.gleam`) already shows a
"Records by Status" section: global per-status counts across all records, regardless of who
they are assigned to. It does not show how that work is distributed across users, nor how much
work is sitting in the unassigned pool.

An admin overseeing the annotation workflow needs to see, at a glance:
- how much work each user currently holds, broken down by state (actively in work, bounced back,
  blocked on prerequisites, failed);
- how many tasks are available in the open pool to be claimed.

"Assigned / pinned" (закреплённый) means `Record.user_id IS NOT NULL`. Assigning a user normally
sets the record to `inwork`, but an assigned record can also legitimately sit in:
- `pending` — invalidated back to pending while keeping `user_id` (soft/hard invalidation);
- `failed` — manually failed (`fail_record` keeps `user_id`);
- `blocked` — created with a `user_id` while required input files are missing.

These four statuses (`inwork`, `pending`, `blocked`, `failed`) are exactly the "active workload"
states of an assigned record. `finished` (done), `preparing` and `pause` (edge states) are out of
scope for this view.

"Available pending" (доступно pending) means `status == pending AND user_id IS NULL` — the pool of
unassigned tasks that can be claimed.

## Goal / scope

Add a new **"Workload by user"** section to the admin dashboard:

1. A table **user × status** over **assigned** records, with columns
   `inwork | pending | blocked | failed` (plus a `Total` column = sum of the four).
   One row per **active** user (`is_active = true`), including users with zero assigned records.
   Rows sorted by email (consistent with the existing Role Matrix).
2. A single **"Available pending"** stat — the count of unassigned `pending` records.

This is a new cut by assignment; it does **not** replace the existing "Records by Status" section.

## Decisions (resolved during brainstorming)

| Question | Decision |
|---|---|
| Granularity | Per-user table (not just a global summary). |
| Rows | All **active** users (`is_active = true`), including zero-workload rows; deactivated users excluded. |
| Status columns | The four named: `inwork`, `pending`, `blocked`, `failed`. Plus a `Total` column. |
| "Available pending" | A single global number (`user_id IS NULL AND status = pending`), not broken down by type/role. |
| Transport | Extend the existing `GET /api/admin/stats` response, **not** a new endpoint. |
| Cell click-through | Non-zero counts link to the records list filtered by that user + status; "Available pending" links to `status=pending` + unassigned. |
| "Available pending" placement | A stat card at the top of the section, table below. |

## Architecture

### Why extend `/api/admin/stats` rather than add an endpoint

`pages/admin.gleam::mutation_success` already re-fetches `/admin/stats` after every inline
assign / unassign / status-change the admin performs. Those mutations are exactly what change the
per-user workload and the available-pending count, so putting the new data in `AdminStats` makes
the table refresh automatically with no extra wiring. The cost is three more sequential queries on
`get_stats()` (a low-traffic admin path) — acceptable.

### Backend

**1. `clarinet/models/admin.py`** — new model + two new fields on `AdminStats`:

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
    # ... existing fields unchanged ...
    available_pending: int = 0            # unassigned pending pool
    workload_by_user: list[UserWorkload] = []
```

Defaults keep the schema additive/backward-compatible.

**2. `clarinet/repositories/record_repository.py`** — two aggregation methods, mirroring the
existing `get_status_counts` / `get_per_type_status_counts` style (`func`, `col`, `distinct` are
already imported; use `col(...).is_(None)` / `.is_not(None)` per the project NULL gotcha):

```python
async def get_assigned_status_counts_by_user(self) -> dict[str, dict[str, int]]:
    """Per assigned user, per status: {str(user_id): {status_value: count}}.

    Only records with a user assigned (user_id IS NOT NULL) are counted.
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
    """Count of pending records with no user assigned (the claimable pool)."""
    query = select(func.count()).where(
        col(Record.user_id).is_(None),
        col(Record.status) == RecordStatus.pending,
    )
    result = await self.session.execute(query)
    return result.scalar() or 0
```

The repo method returns counts for *all* statuses per assigned user (no status filter in SQL,
matching `get_per_type_status_counts`); the service selects the four relevant statuses.

**3. `clarinet/services/admin_service.py`** — extend `get_stats()`:

- `assigned = await self.record_repo.get_assigned_status_counts_by_user()`
- `available_pending = await self.record_repo.count_unassigned_pending()`
- `active_users = await self.user_repo.list_all(is_active=True)` (base-repo field filter; returns
  plain `User` rows — only scalar columns `id`/`email`/`is_active` are read, no lazy relationships)
- Build one `UserWorkload` per active user, filling zeros for missing statuses, keyed by
  `str(user.id)`; sort by `email`.
- Pass `available_pending` and `workload_by_user` into the `AdminStats(...)` constructor.

Queries run sequentially (shared `AsyncSession` — no `asyncio.gather`, per backend CLAUDE.md).

**No DB migration** — only new aggregation queries and additive response fields.

### Frontend

**4. `clarinet/frontend/src/api/models.gleam`** — `UserWorkload` Gleam type and two new fields on
the `AdminStats` record (`available_pending: Int`, `workload_by_user: List(UserWorkload)`).

**5. `clarinet/frontend/src/api/admin.gleam`** — `user_workload_decoder()` and extend
`admin_stats_decoder()` to decode the two new fields.

**6. `clarinet/frontend/src/pages/admin.gleam`** — new `workload_section(stats)`, inserted in
`stats_view` between `status_section` and `roles_section`:

- Top: an "Available pending" card via the existing `admin_stat_card`, clickable to
  `router.Records(dict.from_list([#("status", "pending"), #("user", record_filters.unassigned_user_value)]))`.
- Below: a `<table class="table">` styled like the Role Matrix. Header:
  `User | inwork | pending | blocked | failed | Total`. One `<tr>` per active user (already sorted
  by the backend). Each non-zero count is a link to
  `router.Records(dict.from_list([#("status", <status>), #("user", <user_id>)]))`; zeros render as a
  muted `0`. `Total` = `inwork + pending + blocked + failed`.

Section/column headers are raw English literals (`"Workload by user"`, `"Available pending"`, the
status names), consistent with the rest of `admin.gleam`, which is not localized at the section
level (`status_section` already uses the raw backend status string as the card label). **No new
i18n keys required.**

Client-side filtering already supports both dimensions used by the links: `record_filters`'s
`"user"` key takes a specific `user_id` or the special `unassigned_user_value` (`"__unassigned__"`),
which `records_query.from_filters` maps to the backend `user_id` / `wo_user` filter. So the
click-through links reuse the existing server-side records filtering with no backend changes.

## Data flow & refresh

`GET /api/admin/stats` → `AdminStats` (now carrying `workload_by_user` + `available_pending`) →
`stats_view` renders the table. After an inline assign / unassign / status change by the admin,
`mutation_success` re-fetches `/admin/stats`, so the table and the available-pending card update
automatically.

Changes made outside the admin page (e.g. a user claiming or failing a record elsewhere) are
reflected on the next page load — the same eventual-consistency behavior as the existing stat
cards. Live SSE updates are out of scope.

## Edge cases

- **Active user, zero assigned records** — still rendered, all counts `0` (rows come from the
  active-user list, not from the record aggregation).
- **Deactivated user holding assigned records** — by the "active users only" decision, such a
  user is **not** shown, so their assigned records do not appear in the table. This is an accepted
  visibility gap; if it later matters, the row set can be widened to "active users ∪ users with
  assigned records" without touching the response shape.
- **Record assigned but in `finished` / `preparing` / `pause`** — counted by the repo method but
  ignored by the service (only the four columns are surfaced). Intentional.
- **Zero-count cells** — rendered as a muted `0`, not a link.

## Testing

**Backend**
- Repository unit tests for `get_assigned_status_counts_by_user` (only assigned records counted;
  correct per-user/per-status grouping; unassigned excluded) and `count_unassigned_pending`
  (only `pending` + `user_id IS NULL`).
- Service test for `get_stats()`: active users with no assigned records appear as zero rows;
  deactivated users are excluded; `available_pending` correct; existing fields unchanged.
- Integration test for `GET /api/admin/stats` asserting the new `workload_by_user` /
  `available_pending` fields and admin-only auth.

**Frontend**
- Decoder test: `admin_stats_decoder` parses a payload containing `workload_by_user` and
  `available_pending`.

## Out of scope

- Live (SSE) refresh of the workload table.
- Breaking "available pending" down by record type or role.
- Persistent workload history / time series.
- Showing `finished` / `preparing` / `pause` assigned counts.
