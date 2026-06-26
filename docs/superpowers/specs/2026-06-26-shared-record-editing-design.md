# Shared-record editing for RecordDef (role-scoped, last-editor ownership)

**Date:** 2026-06-26
**Status:** Approved design — pending implementation plan
**Branch:** `worktree-shared-record-editing`

## 1. Problem

Editing a clinical `Record` is gated for non-superusers by three independent layers:

1. **Role access** (`authorize_record_access`, `dependencies.py:512`) — the user must
   hold the record type's `role_name`, otherwise the record is invisible
   (`role_name=None` ⇒ superuser-only).
2. **Ownership** (`authorize_mutable_record_access`, `dependencies.py:554`) — only the
   superuser, the assigned user (`record.user_id == user.id`), or an **unassigned**
   record (`user_id is None`) may be mutated. Anyone else gets 403.
3. **Edit window** (`ensure_record_editable`, `record_service.py:36`) —
   `RecordType.editable` / `edit_window_days` lock finished records for non-superusers.

The ownership layer (#2) blocks legitimate collaboration: a record filled by user A
cannot be edited by user B even when both should be able to. Concretely — in
`clarinet_nir_liver` an `anamnesis` RecordDef is meant to be editable by **any
`admin`**, even when a **superuser** originally filled it. Today a non-superuser
admin gets 403 from layer #2.

Note: the built-in `admin` role (`current_admin_user` = superuser OR member of role
`admin`) currently grants admin-area operations but **no** special power to edit
records owned by others. The frontend `permissions.has_record_permission`
(`permissions.gleam:30`) already returns `True` for admins on any record, so the FE
optimistically shows the edit button while the BE rejects the mutation — a latent
mismatch this feature closes for flagged types.

## 2. Goal / success criteria

- A RecordDef can be marked so that **any user who can access the type (holds its
  `role`) may edit any record of that type**, not only the owner / unassigned ones.
- `anamnesis`: `role="admin"` + the new flag ⇒ any admin edits records filled by
  anyone, including a superuser.
- **Ownership follows the last editor**: each data edit by a real (non-system) user
  reassigns `record.user_id` to that user.
- Backward compatible: flag defaults off; existing record types are unaffected.

## 3. Non-goals (out of scope)

- Separate view-vs-edit role lists (rejected — "editor pool = the type's role").
- Per-user, hand-picked editor lists.
- Communal single-record semantics: no changes to creation, `min_records` /
  `max_records`, or how many records exist per context.
- A "shared" badge / indicator in the records table UI.
- Ownership transfer on status-only changes (`PATCH /status`, claim) — claim already
  assigns; status changes are not "editing data".

## 4. Concept

New boolean `shared_editing` on `RecordType` (default `False`).

- **Authorization:** when `True`, ownership layer #2 is bypassed — any caller that
  passed role layer #1 may mutate. Layers #1 (role) and #3 (edit window) are
  unchanged and orthogonal. With default `editable=True` a finished, superuser-filled
  record stays editable by admins; a project that wants a hard post-submit lock still
  sets `editable=False` (existing semantics).
- **Ownership:** every data write by a real user sets `record.user_id` to that user
  ("last editor owns it"). System / worker / RecordFlow writes (no acting user) leave
  ownership untouched. Per-edit attribution is preserved in `RecordEvent.actor_id`.
- **Constraint:** `shared_editing=True` requires `unique_per_user=False`, enforced
  fail-fast at config load. Ownership churn is contradictory with per-user
  uniqueness (an editor who already owns one record of the type would 409 on the
  next edit).

The editor pool equals the type's role because the access layer (#1) already gates
who sees the record; relaxing only the ownership layer means "whoever can access can
edit". For `anamnesis`, `role="admin"` makes the pool = admins.

## 5. Backend changes

### 5.1 Schema field — `clarinet/models/record_type.py` (`RecordTypeBase`)

Add after `mask_patient_data` (~line 158), following the documented additive-boolean
pattern:

```python
shared_editing: bool = Field(
    default=False,
    sa_column_kwargs={"server_default": sql_expression.false()},
    description=(
        "Whether any user who can access this type (holds its role_name) may edit "
        "any record of this type, not only the owner/unassigned. Each data edit "
        "reassigns ownership (record.user_id) to the editing user. Requires "
        "unique_per_user=False."
    ),
)
```

Inherited automatically by `RecordTypeCreate`, `RecordTypeRead`, and the `RecordType`
table model.

### 5.2 Config primitive — `clarinet/config/primitives.py` (`RecordDef`)

Add `shared_editing: bool = False` (~line 156) and a matching docstring line.

### 5.3 Loader — `clarinet/config/python_loader.py` (`_to_record_type_create`)

The loader forwards fields explicitly via `model_fields_set`. Add (~line 218):

```python
if "shared_editing" in rt_def.model_fields_set:
    kwargs["shared_editing"] = rt_def.shared_editing
```

### 5.4 Reconciler — `clarinet/config/reconciler.py` (`_COMPARED_FIELDS`)

Add `"shared_editing"` to the tuple (~line 49) so config toggles are detected and
applied to existing rows.

### 5.5 Authorization gate — `clarinet/api/dependencies.py` (`authorize_mutable_record_access`)

```python
if user.is_superuser:
    return record
if record.user_id is None or record.user_id == user.id:
    return record
if record.record_type.shared_editing:        # NEW
    return record
raise AuthorizationError("Insufficient permissions to modify this record")
```

Sound because this dependency consumes `AuthorizedRecordDep` (layer #1 already
passed) and `record.record_type` is eager-loaded (the chain already reads
`record_type.role_name`).

### 5.6 Read DTO — `clarinet/models/record.py` (`RecordRead`)

Mirror the existing `is_editable` computed field (~line 440):

```python
@computed_field  # type: ignore[prop-decorator]
@property
def shared_editing(self) -> bool:
    """Mirror RecordType.shared_editing for the frontend permission check."""
    return self.record_type.shared_editing
```

### 5.7 Repository — `clarinet/repositories/record_repository.py` (`update_data`, line 634)

Add a keyword param for atomic ownership transfer (no `status` side effect, unlike
`assign_user`):

```python
async def update_data(
    self,
    record_id: int,
    data: RecordData,
    new_status: RecordStatus | None = None,
    *,
    reassign_to: UUID | None = None,
) -> tuple[Record, RecordStatus]:
    record = await self.get(record_id)
    old_status = record.status
    record.data = data
    if new_status is not None:
        record.status = new_status
    if reassign_to is not None:
        record.user_id = reassign_to
    await self.session.commit()
    return await self.get_with_relations(record_id), old_status
```

### 5.8 Service — `clarinet/services/record_service.py`

**`submit_data` (line 437):** keep the existing `record_check.user_id is None`
auto-assign branch unchanged. Add a shared-transfer branch:

```python
transfer_to: UUID | None = None
if user_id is not None:
    record_check = await self.repo.get_with_record_type(record_id)
    if record_check.user_id is None:
        ...  # existing auto-assign path (unchanged)
    elif record_check.record_type.shared_editing and record_check.user_id != user_id:
        transfer_to = user_id
        await self._record_event(
            record_id=record_id, kind="assigned", actor_id=actor_id,
            new_value={"user_id": str(user_id), "via": "shared_submit"},
        )
    else:
        await self.repo.ensure_user_assigned(record_id, user_id)

self._mark_audit(record_id, actor_id)
record, old_status = await self.repo.update_data(
    record_id, data, new_status=new_status, reassign_to=transfer_to,
)
```

The transfer rides the existing data-write commit (no extra round-trip).

**`update_data` (line 512):** currently fetches the record only for non-superusers.
Fetch whenever `acting_user is not None` (needs `record_type`), then:

```python
transfer_to = None
if acting_user is not None:
    record = await self.repo.get_with_relations(record_id)
    if not acting_user.is_superuser:
        ensure_record_editable(record, acting_user)
    if record.record_type.shared_editing and record.user_id != acting_user.id:
        transfer_to = acting_user.id
        await self._record_event(
            record_id=record_id, kind="assigned", actor_id=actor_id,
            new_value={"user_id": str(acting_user.id), "via": "shared_update"},
        )
self._mark_audit(record_id, actor_id)
record, old_status = await self.repo.update_data(record_id, data, reassign_to=transfer_to)
```

System calls (`acting_user is None`) keep the current behaviour: no fetch, no
transfer. `_check_unique_per_user` is unnecessary on the transfer path — the
fail-fast invariant guarantees `unique_per_user=False` for shared types, so it would
no-op anyway.

All record-data endpoints (`POST/PATCH /records/{id}/data`, `POST/PATCH
/records/{id}/submit`) funnel through `_process_submission` (`record.py:455`) into
`submit_data` / `update_data`, so both touchpoints cover every write path.

### 5.9 Fail-fast validation — `clarinet/utils/bootstrap.py` (`reconcile_config`, line 339)

Alongside the existing `role_name` (389-401) and `allowed_viewers` (416-428) checks,
add:

```python
shared_unique = [
    item.name for item in all_items
    if item.shared_editing and item.unique_per_user
]
if shared_unique:
    raise ConfigurationError(
        "shared_editing requires unique_per_user=False; offending record types: "
        f"{shared_unique}"
    )
```

Converts a confusing runtime 409 into a clear startup error and upholds the
fail-fast contract.

### 5.10 Migration

`make db-migration` (alembic autogenerate) → additive
`shared_editing BOOLEAN NOT NULL DEFAULT false`. `server_default=sql_expression.false()`
keeps the `ALTER TABLE` safe on populated PostgreSQL. Covered by
`TestServerDefaultsForAdditiveMigrations` and the populated-table PG/SQLite migration
tests.

## 6. Frontend changes

### 6.1 `clarinet/frontend/src/api/models.gleam`

Add `shared_editing: Bool` to the `Record` type (constructor + every record literal).

### 6.2 Decoders — `clarinet/frontend/src/api/series.gleam` (~line 176) and `records.gleam`

`use shared_editing <- decode.optional_field("shared_editing", False, decode.bool)`
and thread it into every `models.Record(...)` construction site (grep all
`models.Record(` builders; mirror how `is_editable` is threaded).

### 6.3 `clarinet/frontend/src/utils/permissions.gleam` (`has_record_permission`, line 30)

```gleam
pub fn has_record_permission(user: Option(User), record: Record) -> Bool {
  case user {
    Some(u) ->
      is_admin_user(u)
      || record.user_id == Some(u.id)
      || record.user_id == option.None
      || record.shared_editing            // NEW
    _ -> False
  }
}
```

Sound because the backend list/find endpoints already restrict returned records to
types whose role the user holds — a returned `shared_editing` record means the user
is in the edit pool. This also feeds `can_fill_record` and `can_edit_record`
unchanged.

### 6.4 Build

`make frontend-build` → regenerate `clarinet/static/clarinet_frontend.js`.

For the specific `anamnesis` (`role="admin"`) case the FE already shows the buttons
to admins via `is_admin_user`; 6.1–6.3 make the feature correct for non-admin shared
roles and remove the FE-optimistic / BE-strict mismatch.

## 7. Data flow (anamnesis example)

1. Superuser fills `anamnesis` → `user_id = superuser`, status `finished`
   (default `editable=True`).
2. Admin B opens it: backend returns it (role `admin` ∈ B's roles); FE shows edit
   (`has_record_permission` true; `can_edit_record` true because `is_editable` true).
3. B submits an edit → layer #1 ok (role) → layer #2 bypassed (`shared_editing`) →
   layer #3 ok (`editable`) → service transfers `user_id = B`, writes data, audits
   `data_updated` + `assigned(via=shared_update)`.
4. Admin C edits later → `user_id = C`. Full edit history preserved in `RecordEvent`
   (`actor_id` per event).

## 8. Testing

- **Backend authz:** `authorize_mutable_record_access` admits a non-owner role-holder
  when `shared_editing=True`, 403 when `False`; superuser / owner / unassigned paths
  unaffected.
- **Service:** `submit_data` and `update_data` transfer ownership to the editor for
  shared types and emit the `assigned` event; no transfer for non-shared types, for
  the current owner, or for system calls (`acting_user=None`).
- **Fail-fast:** config with `shared_editing=True, unique_per_user=True` raises
  `ConfigurationError` at load.
- **Reconciler:** toggling `shared_editing` is detected as a change.
- **Frontend (gleeunit):** `has_record_permission` honours the flag; the `Record`
  decoder round-trips `shared_editing`.
- **Migration:** existing server-default regression suite covers the new column.

## 9. Affected files (checklist)

Backend:
- `clarinet/models/record_type.py` — `shared_editing` field (+`server_default`)
- `clarinet/config/primitives.py` — `RecordDef.shared_editing`
- `clarinet/config/python_loader.py` — forward in `_to_record_type_create`
- `clarinet/config/reconciler.py` — `_COMPARED_FIELDS`
- `clarinet/api/dependencies.py` — `authorize_mutable_record_access` gate
- `clarinet/models/record.py` — `RecordRead.shared_editing` computed field
- `clarinet/repositories/record_repository.py` — `update_data(reassign_to=...)`
- `clarinet/services/record_service.py` — ownership transfer in `submit_data` /
  `update_data`
- `clarinet/utils/bootstrap.py` — fail-fast validation
- `alembic/versions/*` — additive migration

Frontend:
- `clarinet/frontend/src/api/models.gleam`
- `clarinet/frontend/src/api/series.gleam`, `clarinet/frontend/src/api/records.gleam`
- `clarinet/frontend/src/utils/permissions.gleam`
- `clarinet/static/clarinet_frontend.js` (build artifact)

Tests + docs:
- backend tests (authz gate, service transfer, fail-fast, reconciler)
- `clarinet/frontend/test/*` (permissions + decoder)
- `.claude/rules/recordflow-dsl.md`, `clarinet/config/CLAUDE.md` (RecordDef field list)
- `.claude/rules/api-deps.md` (gate note)

## 10. Decisions (resolved)

- **Editor pool = the type's `role`** (Option A) — reuses the existing role-access
  layer; one flag, minimal surface.
- **Flag name:** `shared_editing`.
- **Frontend:** full support (correct for non-admin shared roles, not admin-only).
- **Ownership:** last editor becomes owner; history in `RecordEvent`.
- **Fail-fast** on `shared_editing=True` + `unique_per_user=True`.
