## Why

Three downstream RecordTypes each hand-rolled a different workaround for one missing
framework primitive — a per-parent cardinality axis. One workaround silently drops 12.6%
of `second-review` records (~233 unreviewed lesions) because `unique_per_user` counts per
(series, user) when the record's true identity is per **parent segmentation**. The
counting bug is entangled with a latent file-path collision: `unique_per_user=True` was
*accidentally* holding the output-path invariant, so fixing the count without fixing the
pattern would trade a countable failure for silent `.seg.nrrd` clobbering. Refactor the
framework so this class of fix becomes a config edit plus a startup fail-fast, not a
workaround with a comment explaining why the framework can't express the intent.

## What Changes

**Uniqueness constraint**
- From: `unique_per_user: bool` — the only configurable uniqueness axis; counts one record per (level-context, user).
- To: `unique_by: frozenset[Literal["user","parent"]] | None = frozenset({"user","parent"})` — a set of relationship partitions applied **within the record's own level context** (the level scope is implicit, never a partition). `None` means no uniqueness; an empty set is rejected at config load; `False` is accepted as the TOML spelling of `None` (TOML has no null) and `True` is rejected with a teaching message. `unique_per_user` becomes a deprecated compat input translated on **every** construction path — `RecordDef` kwarg, TOML documents, API create/patch bodies (`True→{"user"}`, `False→None`, warned).
- Reason: the true identity of parented records is per-parent, inexpressible with a per-user boolean; and making the counting universe implicit (the record's level) removes the ill-defined "count across levels" combinations.
- Impact: breaking at the model layer (column replaced); behavior-preserving migration for existing rows; downstream configs keep working via the compat kwarg.

**`max_records` semantics**
- From/To: unchanged — remains a quota counted per **level context**, orthogonal to `unique_by` (proven necessary by `first-check`, which needs quota-2-per-study AND unique-1-per-user simultaneously). One-record-per-level is spelled `max_records=1` with `unique_by=None` — the single canonical spelling.
- Reason: parented types get "1 per parent" from `unique_by={"parent"}`'s implicit cap of 1, retiring the `max_records=10000` workarounds.

**Output-path uniqueness**
- To: new `{parent_id}` file-pattern placeholder (resolved from the `record.parent_record_id` FK column); new config-load validator requiring a singular OUTPUT pattern to discriminate every partition in `unique_by`, plus the record's level UID when `FileDef.level` is coarser than the record's level, plus `{id}` when `unique_by=None` and `max_records` permits >1. A pattern containing `{id}` always passes; `multiple=True` FileDefs exempt; explicit **per-FileRef** opt-out (`allow_path_collision`) for author-known-unique `{data.*}` discriminators — `{origin_type}` counts as a parent discriminator only under that opt-out (it distinguishes parent types, not two same-type parents).
- Reason: prevents two records the constraints permit from resolving to the same file.
- Impact: non-breaking (zero current violations across 55 RecordDefs); fails fast at startup on misconfiguration.

**Parent link lifecycle**
- From: `parent_record_id` FK is `ON DELETE SET NULL`.
- To: `ON DELETE CASCADE`.
- Reason: `{parent_id}` must be stable; a null-ed parent shifts a record's own file path. The one application delete path already cascades the subtree, so app behavior is unchanged.
- Impact: bypass deletes (raw SQL/restore) now remove children silently (no `RecordEvent`) instead of orphaning them; changelog note + constraint migration required.

**Enforcement surface**
- From: uniqueness enforced in three places, all keyed to the boolean — route-level `check_constraints`, two service-layer re-checks (`inherit_user_from_parent`, user assign/claim), and the claimable-pool SQL filter `_unique_per_user_violation_filter` (5 call sites referencing the `unique_per_user` column directly).
- To: one partition-aware `ensure_unique_by` behind create/claim/assign/inheritance; the pool filter prefetches partitioned type-name sets (portable SQL, NULL-safe parent match). The check runs only on a fully bound partition tuple (`parent` always binds, `user` only when `user_id` is set) — unassigned claiming pools keep working; assignment closes the user axis.
- Reason: enforcement and claim eligibility must read the same key, or the pool lists records a claim will 409 on — and the SQL filter breaks outright when the column is dropped.
- Impact: repo/service signatures change internally; behavior-preserving for migrated types (unassigned creation stays permitted).

**SQLite FK enforcement**
- From: production SQLite connections never set `PRAGMA foreign_keys` — FK actions (today's SET NULL, the new CASCADE, `RecordFileLink` cascade) silently don't fire; tests set the pragma, PostgreSQL always enforces.
- To: `PRAGMA foreign_keys=ON` on every SQLite connection (extending the existing `_set_sqlite_pragmas` hook) + a startup `PRAGMA foreign_key_check` audit logging violations as warnings.
- Reason: the cascade guarantee must be engine-independent; git history confirms the pragma was never deliberately disabled — a parity oversight vs. tests and PG.
- Impact: legacy SQLite DBs with dangling rows surface as warnings and write-time errors; changelog note. No kill-switch — PG never had one.

## Capabilities

### New Capabilities
- `record-cardinality-constraints`: the `unique_by` partition set (`{user, parent}` or None) applied within the implicit level scope, its canonicalization (incl. `False`-as-off, empty-set rejection), `max_records` as orthogonal per-level quota, `unique_per_user` deprecation on every input path + behavior-preserving migration, the NULL-user binding rule (unassigned pools), and creation- **and assignment-time** enforcement including claimable-pool filtering.
- `output-path-uniqueness`: the `{parent_id}` placeholder resolution and the config-load validator that a RecordType's singular OUTPUT patterns discriminate every coexisting-record dimension, including the `{id}` fast-pass, the `multiple=True` exemption, and the per-FileRef opt-out (`{origin_type}` accepted only under it).
- `parent-record-cascade`: `parent_record_id` `ON DELETE CASCADE`, the guarantee that deleting a parent removes its child records, and SQLite FK enforcement (connection pragma + startup audit) making that guarantee engine-independent.

### Modified Capabilities
<!-- none — no existing specs in openspec/specs/ -->

## Impact

- **Models**: `clarinet/models/record_type.py` (`unique_by` field replacing `unique_per_user`, model validators, legacy-input translation on `RecordTypeCreate`/`RecordTypeOptional`), `clarinet/models/record.py` (FK `ondelete`), `clarinet/models/file_schema.py` (`allow_path_collision` on `RecordTypeFileLink` + `FileDefinitionRead`).
- **Config**: `clarinet/config/primitives.py` (`RecordDef.unique_by` + `unique_per_user` compat kwarg, `FileRef.allow_path_collision`), `clarinet/config/python_loader.py` (`_to_record_type_create` forwards `unique_by`), `clarinet/config/toml_exporter.py` (field list swap, `false` for None), both loaders invoke the path validator; reconciler compares canonical form (both sides via `canonical_unique_by`, explicit-`None` honored).
- **Repositories**: `clarinet/repositories/record_repository.py` (`check_constraints`, partition-aware `ensure_unique_by`, pool filter rewritten over prefetched type-name sets — 5 call sites).
- **Services**: `clarinet/services/record_service.py` (inheritance re-check + assign/claim checks route through `ensure_unique_by`), `clarinet/services/record_type_service.py` (PATCH validates the merged result).
- **Engine**: `clarinet/utils/db_manager.py` (`PRAGMA foreign_keys=ON` in `_set_sqlite_pragmas`, startup `foreign_key_check` audit, `_pydantic_json_serializer` learns set/frozenset).
- **Files**: `clarinet/files/_patterns.py` (`{parent_id}` in `fields_from`).
- **Frontend**: `clarinet/frontend/src/api/models.gleam` + `records.gleam` decoders/encoders, `pages/record_types/edit.gleam` (replace the `unique_per_user` toggle with a `unique_by` control).
- **Migration**: downstream projects generate an alembic migration (column swap with the `True→{user}`/`False→None` backfill, FK constraint alter, `recordtypefilelink.allow_path_collision`); framework ships the model/`server_default` change.
- **Docs**: `.claude/rules/*.md` + byte-identical copies under `clarinet/docs/` (golden-guard test), CHANGELOG (incl. SQLite FK enforcement note).
- **Out of scope**: `clarinet_nir_liver` config edits (the follow-up that consumes this).
