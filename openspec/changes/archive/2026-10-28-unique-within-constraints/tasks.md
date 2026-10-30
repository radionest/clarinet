## 1. Canonicalization helper (foundation)

- [x] 1.1 Add a `unique_by` canonicalizer over `{"user","parent"}`: `None` stays `None` (no uniqueness); `False` → `None` (the TOML spelling of off); `True` raises with a teaching message naming `{"user"}` and omission; a set/list is validated (unknown token → error), de-duped, sorted to a canonical tuple; an **empty** set raises with a teaching message naming `None` and `max_records=1`
- [x] 1.2 Add the shared legacy-mapping helper (`unique_per_user` `True→{"user"}`, `False→None`) used by both `RecordDef` and the model before-validators
- [x] 1.3 Unit-test: order-insensitivity, dedupe, `None` passthrough, `False`→`None`, `True` rejection, empty-set rejection, unknown-token rejection, legacy mapping

## 2. Model layer (`clarinet/models/record_type.py`, `record.py`, `file_schema.py`)

- [x] 2.1 Add `unique_by` column to `RecordTypeBase` (nullable JSON via `PortableJSON`; stores `null` for `None` or a canonical sorted list; `server_default '["parent","user"]'` matching the model default `{"user","parent"}`); remove the `unique_per_user` column; re-point `_validate_shared_editing` to `"user" in (self.unique_by or ())`
- [x] 2.2 Add `mode="before"` validators on `RecordTypeCreate` AND `RecordTypeOptional` translating a legacy `unique_per_user` key (`True→{"user"}`, `False→None`, explicit `unique_by` wins, DeprecationWarning) — covers TOML documents and API bodies, never silently ignored
- [x] 2.3 Add a `@model_validator` enforcing the output-path uniqueness predicate on `RecordTypeCreate` (mirror `_validate_shared_editing`), reachable by API and config writes
- [x] 2.4 Add `allow_path_collision` to `RecordTypeFileLink` (bool column, `server_default` false) + `FileDefinitionRead` field, threaded through `RecordType.file_registry`
- [x] 2.5 Extend `_pydantic_json_serializer` (`db_manager.py:29`) to serialize `set`/`frozenset` as sorted lists so a frozenset reaching a JSON-column flush cannot raise
- [x] 2.6 Change `record.py` `parent_record_id` FK `ondelete="SET NULL"` → `"CASCADE"`
- [x] 2.7 Extend the server-default metadata guard (`tests/migration/test_schema_integrity.py`) to cover the new nullable JSON column's default and the new link-table boolean

## 3. Config primitives & loader (`clarinet/config/primitives.py`, `python_loader.py`)

- [x] 3.1 Add `RecordDef.unique_by` (`frozenset|None`, default `{"user","parent"}`, canonicalized in a validator); accept `unique_per_user` as a deprecated compat kwarg via the shared mapping helper; explicit `unique_by` wins
- [x] 3.2 Add `FileRef(..., allow_path_collision=True)` param, carried into `fileref_to_file_definition`
- [x] 3.3 `_to_record_type_create` (`python_loader.py:209`): replace the `unique_per_user` forwarding branch with `unique_by` (gated on `model_fields_set`)
- [x] 3.4 Unit-test the compat mapping (True/False/unspecified/both-set), the deprecation warning, and that a Python-mode `unique_by` reaches the resulting `RecordTypeCreate`

## 4. File pattern placeholder (`clarinet/files/_patterns.py`)

- [x] 4.1 Add `"parent_id": record.parent_record_id` to `fields_from` (FK column, no parent fetch)
- [x] 4.2 Unit-test `{parent_id}` renders the FK value and is load-independent (same result with and without a loaded parent); `None` parent renders empty under lenient mode; confirm `{origin_type}` unchanged

## 5. Enforcement surface (`record_repository.py`, `record_service.py`)

- [x] 5.1 Replace `ensure_unique_per_user` with partition-aware `ensure_unique_by(record_type, *, user_id, parent_record_id, patient_id, study_uid, series_uid)`: always scoped to the record's level context; **bound-tuple rule** — skip entirely when `"user"` is selected and `user_id` is None (unassigned pools; invariant enforced at assignment), `"parent"` always bound and NULL-safe (`IS NULL` when None); `None` → no check; keep `max_records` per-level counting untouched; preserve 409 codes (`UNIQUE_PER_USER`, `RECORD_LIMIT_REACHED`)
- [x] 5.2 `check_constraints` passes `parent_record_id` into the uniqueness count and invokes it whenever `unique_by` is non-`None`; delete `count_user_records_for_context` if now unused
- [x] 5.3 Route the service call sites through `ensure_unique_by`: the `inherit_user_from_parent` re-check (`record_service.py:219`, now passing `record.parent_record_id`) and `_check_unique_per_user` (`:1137`, callers `:331/:368/:464`) with the candidate record's own `parent_record_id`
- [x] 5.4 Rewrite `_unique_per_user_violation_filter` (`:326`) as `_unique_by_violation_filter(user_id, user_types, user_parent_types)` over prefetched type-name sets (one SELECT on `recordtype`), NULL-safe parent match in the EXISTS; update all five call sites (`:545`, `:596`, `:1425`, `:1679`, `:1822`)
- [x] 5.5 Integration-test: parent partition (two parents → two records; same parent → 409); parentless default collapses to per-user; quota + uniqueness together (first-check shape); `unique_by=None` → only `max_records` binds; unassigned pools coexist for user-selecting types; `{"parent"}`-only dedupes unassigned children; an unassigned same-parent record does not block an assigned creation; claim rejected on same-partition match, allowed on different parent; claimable listing agrees with claim outcome

## 6. Path-uniqueness validator (config load + PATCH)

- [x] 6.1 Implement the predicate: `{id}` in the pattern → pass; else for each singular OUTPUT FileDef require a discriminator per `unique_by` partition (`user`→`{user_id}`; `parent`→`{parent_id}` only when `parent_required`, with `{origin_type}` counting **only under the opt-out**), the record level UID when `FileDef.level` is coarser than the record level, and `{id}` when `unique_by=None` and `max_records>1`; exempt `multiple=True`; skip FileDefs whose binding carries `allow_path_collision` (siblings stay validated)
- [x] 6.2 Wire the validator into both loaders (Python `python_loader` + TOML path) so a violation raises `ConfigLoadError` → `StartupError`, naming the RecordType and FileDef
- [x] 6.3 `RecordTypeService.update` validates the merged result (effective `RecordTypeCreate` after applying the PATCH) so a mutation cannot poison the next startup
- [x] 6.4 Tests: parent-without-`{parent_id}` rejected; with `{parent_id}` passes; `{origin_type}` alone rejected without opt-out; `unique_by=None`+`max>1`-without-`{id}` rejected; coarser-FileDef-level requires explicit UID; `{id}` fast-pass; `multiple=True` exempt; per-file opt-out suppresses while a sibling FileDef still fails; PATCH to a violating combo rejected

## 7. Config reconciler

- [x] 7.1 Compare `unique_by` canonically: canonicalize BOTH sides via `canonical_unique_by` (DB rows hold the raw JSON list — `table=True` skips validators); reorder = no-op; `None` vs a set is a real change; drift heals toward config
- [x] 7.2 An explicit `unique_by=None` (in `model_fields_set`) must bypass the config-None-vs-default skip in `_fields_differ` (`reconciler.py:133`) and write `NULL` over a default-valued row; add reconciler tests for reorder no-op, explicit-None write, and heal-when-unset

## 8. TOML exporter & legacy TOML compat (`toml_exporter.py`, `bootstrap.py`)

- [x] 8.1 Replace `unique_per_user` in the exporter field list (`toml_exporter.py:27`) with `unique_by`, writing `false` for `None` and the canonical sorted list otherwise
- [x] 8.2 Test: a legacy `.toml` carrying `unique_per_user = false` loads as `unique_by=None` with a deprecation warning (via the `RecordTypeCreate` before-validator); an off-type survives an export → reload round-trip as off

## 9. SQLite FK enforcement (`db_manager.py`, lifespan)

- [x] 9.1 Add `PRAGMA foreign_keys=ON` to `_set_sqlite_pragmas` (`db_manager.py:92`)
- [x] 9.2 Startup audit: run `PRAGMA foreign_key_check` against SQLite during init, logging each violation as WARNING without aborting
- [x] 9.3 Tests: a connection from `DatabaseManager` reports `foreign_keys=1`; a DB-level parent DELETE cascades to children on SQLite; a seeded dangling row logs a warning and startup completes

## 10. Frontend (`clarinet/frontend/src`)

- [x] 10.1 Replace `unique_per_user: Bool` with `unique_by: Option(List(String))` in `api/models.gleam` (both records) and the decoders/encoders in `api/records.gleam`
- [x] 10.2 `pages/record_types/edit.gleam`: replace the `unique_per_user` toggle (form schema `:143`, PATCH body `:242`) with a `unique_by` control offering the canonical spellings (default / user / parent / user+parent / off → `null`)
- [x] 10.3 `make frontend-check` + frontend tests pass

## 11. Examples, docs, changelog

- [x] 11.1 Update `examples/demo` and `examples/project_template` RecordDefs to `unique_by` (drop `unique_per_user`), demonstrating a parent-scoped type (`unique_by={"parent"}` + `{parent_id}` OUTPUT pattern)
- [x] 11.2 Update `.claude/rules/*.md` (recordflow-dsl, record-repo, file-registry, config, models) AND the byte-identical copies under `clarinet/docs/*` (golden-guard); document `unique_by` (partitions within implicit level scope, `None`=off, `False`=TOML off, `max_records=1`=one-per-level), the bound-tuple rule (unassigned pools enforced at claim; `{"parent"}`-only types enforce at creation), `{parent_id}` vs `{origin_type}` (exact vs opt-out-only), and the per-FileRef opt-out
- [x] 11.3 CHANGELOG entry: `unique_by` replacing `unique_per_user` (+ migration mapping `True→{user}`, `False→None`), the path-uniqueness fail-fast, the FK CASCADE audit trade, and SQLite FK enforcement (pragma + startup audit)
- [x] 11.4 Add a follow-up note (proposal/design already scope it) that downstream `clarinet_nir_liver` must generate the alembic migration (incl. `recordtypefilelink.allow_path_collision`), edit configs, and backfill the 25 dropped reviews

## 12. Full verification

- [x] 12.1 `make check` (format + lint + typecheck) clean
- [x] 12.2 `./scripts/run_tests.sh -k "constraint or unique or path or claim or reconcil" -q` then `make test-fast`; confirm no regression in existing `unique_per_user`/`max_records` tests (updated to `unique_by`)
- [x] 12.3 Grep confirms no lingering `unique_per_user` references outside the compat inputs and tests: `grep -rn "unique_per_user" clarinet/ # bash-ok` (expected: uniqueness helper mapping, model before-validators, `RecordDef` kwarg, tests) and `grep -rn "unique_per_user" clarinet/frontend/src # bash-ok` (expected: none)
