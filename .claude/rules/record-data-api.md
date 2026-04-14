---
globs: plan/workflows/**
---
# Record data API methods

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
