---
globs: plan/workflows/**
---
# Record data API methods

## `record.data` — structured form payload

| Method | HTTP | Precondition | Transitions to | Triggers flows |
|---|---|---|---|---|
| `submit_record_data(id, data)` | POST | pending/blocked | finished | `on_status()` |
| `update_record_data(id, data)` | PATCH | finished | finished | `on_data_update()` |
| `prefill_record_data(id, data, method=)` | POST/PUT/PATCH | pending/blocked | stays | none |

`prefill_record_data` methods:
- `POST` (default) — fails if data already exists
- `PUT` — replaces all data
- `PATCH` — merges into existing data

Choose `method` based on `record.data`: use `PATCH` if data exists, `POST` otherwise.

## `record.context_info` — narrow markdown sidecar (NOT `record.data`)

`context_info` is an **independent** field for free-form, human-readable context (markdown) — instructions, links, prior discussion that explain the record to a future user. It is **not** part of the structured `data` payload, has no schema, and does not gate workflow transitions.

Endpoint:

| Endpoint | Body | Auth | Notes |
|---|---|---|---|
| `PATCH /api/records/{id}/context-info` | `{"context_info": str \| null}` | superuser, owner, or any authorised user when record is unassigned | Replaces the field. Pass `null` to clear |

Server-side:
- Source: `record.context_info` (markdown). Rendered HTML: `record.context_info_html` on the response, sanitized via `bleach` allowlist (markdown → HTML → sanitize).
- Use `RecordContextInfoUpdate` (Pydantic) as the request body — do NOT reuse `RecordUpdate` / `RecordData`.
- Backed by `repo.update_fields(record_id, {"context_info": body.context_info})` — does not touch `record.data` or status.
- No RecordFlow triggers fire. If you need behavior on context change, add a separate endpoint — do not piggyback on this one.

Frontend:
- Read `record.context_info_html` (already sanitized) and inject via `attribute.property("innerHTML", json.string(html_str))`. See `.claude/rules/frontend.md` §11.5 (Inserting Server-Sanitized HTML).

When **NOT** to use `context_info`:
- Anything machine-readable / form-driven → goes into `record.data` via the methods above.
- Anything that should drive workflow → use `record.status` transitions, not free-form notes.
