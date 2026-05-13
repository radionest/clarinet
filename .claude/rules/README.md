# Path-Scoped Rules — Topic Index

Each rule file declares a `paths:` frontmatter glob and is auto-loaded when Claude reads or edits matching files. Rules without `paths:` load unconditionally.

When adding a new rule: pick a focused topic, set `paths:` to the directory or file pattern where it applies, keep the file under ~150 lines. Refer back here from `CLAUDE.md` rather than duplicating the list.

## Index

| Rule file | Topic | Triggers on |
|---|---|---|
| `api-deps.md` | DI aliases, RBAC, factory patterns, DICOMweb endpoints | `clarinet/api/dependencies.py`, `clarinet/api/routers/**` |
| `api-urls.md` | Full endpoint URL table with status codes and auth | routers, tests |
| `ci-debugging.md` | gh CLI / GitHub Actions debugging workflow | `.github/workflows/**` |
| `e2e-tests.md` | Frontend stack, VM sub-path, Playwright selectors | `deploy/test/e2e/**` |
| `file-registry.md` | File definition M2M system | `file_schema.py`, file definition repo |
| `frontend-page-contract.md` | MVU page contract, Shared/OutMsg, effects, LoadStatus, cache, errors | `clarinet/frontend/src/**/*.gleam`, test |
| `frontend-routing-forms.md` | API layer, routing under sub-path, list pattern, forms, server HTML | `clarinet/frontend/src/**/*.gleam`, test |
| `frontend-reference.md` | Decoder gotchas, logging, common pitfalls, toolchain | `clarinet/frontend/src/**/*.gleam`, test |
| `logging-pii.md` | Sanitize Referer/Origin before logging, loguru `extra=` quirk | `auth_config.py`, `logger.py` |
| `pipeline-ops.md` | Pipeline settings, testing, dependencies | `clarinet/services/pipeline/**` |
| `pr-review.md` | Project-specific PR review checklist | used by `pr-diff-reviewer` subagent |
| `project-setup.md` | Project init, settings, `plan/` structure | `settings.toml`, `plan/**` |
| `record-data-api.md` | submit/update/prefill data flow + `context_info` markdown sidecar | `plan/workflows/**` |
| `record-data-validator.md` | Python validators for cross-field/cross-element RecordData invariants | `plan/validators*.py` |
| `record-repo.md` | Specialized methods, invalidation, auto_id | record repositories |
| `recordflow-dsl.md` | Full RecordFlow DSL API reference | `recordflow/**`, `*_flow.py` |
| `schema-hydration.md` | Dynamic field options resolver | `schema_hydration.py`, hydrators |
| `schemathesis.md` | Property-based testing guide, boundary-value handling | `tests/schema/**` |
| `slicer-context.md` | Slicer context builder & hydration | `context*.py`, hydrators |
| `slicer-helper-api.md` | SlicerHelper full API + VTK pitfalls | `clarinet/services/slicer/helper.py` |
| `test-debugging.md` | jq recipes for test/log analysis | `tests/**` |

This README is not auto-loaded — it's a reference for humans (and Claude on demand) to locate the rule that owns a topic.
