## Context

`clarinet` is an imaging-centric clinical-research framework (FastAPI + SQLModel, async).
A `RecordType` today declares two cardinality controls:

- `max_records: int | None` — quota per **level context** (patient/study/series, selected
  by `RecordType.level`), enforced by `RecordRepository.count_by_type_and_context`.
- `unique_per_user: bool` — at most one record per (level-context, user), enforced by
  `count_user_records_for_context`. Both are checked in `check_constraints`
  (`record_repository.py:1198`), called from `record.py:343` before every create.

`check_constraints` **already receives `parent_record_id`** (used only for the
`parent_required` check) — the plumbing to count on the parent axis exists but is unused.

Downstream, three `clarinet_nir_liver` RecordTypes want "one record per parent record"
and each faked it: `compare-with-projection` (`max=10000`), `second-review`
(`max=10000` + `unique_per_user`, silently drops 25/198 pairs), `lesion-mapping`
(`max=20` + a callback that reimplements per-parent uniqueness). See `brainstorm.md` for
the full incident, the empirical scan, and the decision chain.

The counting axis and output-file identity are entangled: `unique_per_user=True`
accidentally held the path invariant (one record per (series,user) == one file per
(series,user) for pattern `second_review_{user_id}.seg.nrrd`). Any fix must move both.

## Goals / Non-Goals

**Goals:**
- A configurable uniqueness key over relationship partitions `{user, parent}` — applied
  within the record's own level context — that subsumes `unique_per_user`.
- Keep `max_records` as an orthogonal per-level quota (no behavior change); one canonical
  spelling per intent, no redundant/contradictory states.
- A `{parent_id}` file placeholder + a config-load validator so the constraints and the
  output-path identity can never silently disagree.
- Make `parent_record_id` a trustworthy discriminator (stable under deletion).
- Behavior-preserving migration; downstream configs keep working via a compat kwarg.
- Net deliverable: the downstream fix becomes a config edit + a startup fail-fast.

**Non-Goals:**
- Fixing `clarinet_nir_liver` configs (explicit out-of-scope follow-up).
- Cross-level uniqueness (global-per-user, one-child-per-parent-across-contexts) — designed
  out by making the level scope implicit; if ever needed it is a positive `scope:` field
  with explicit values, not a droppable axis.
- A configurable key for `max_records` (N>1 per parent) — no domain need; future extension.
- Backfilling the 25 already-dropped `second-review` records (downstream one-off).

## Decisions

### D1 — Counting axis is a new orthogonal field, NOT a new `level` value
- **Choice:** keep `DicomQueryLevel` as pure DICOM placement; add `unique_by`.
- **Rationale:** `level` answers placement (UIDs, working dir, viewer, anon path,
  RecordFlow tree filter) — 6/8 consumers would need a parent dereference for a `RECORD`
  level, and `files/facade.py:53` keys working dirs by PATIENT/STUDY/SERIES only.
- **Alternative rejected:** `level="RECORD"` — makes the nullable `parent_record_id` FK
  load-bearing for path resolution; a parent delete would leave level undefined.

### D2 — `unique_by`: relationship partitions within an implicit level scope
- **Choice:** `unique_by: frozenset[Literal["user","parent"]] | None`, default
  `frozenset({"user","parent"})`. Semantics: **≤1 record per distinct combination of the
  selected partitions' values, within the record's own level context.** The level context
  is always part of the uniqueness scope and is never a partition. `None` = no uniqueness.
- **Rationale:** `parent` and `user` are flat equality partitions; `level` is a *scope*
  (a nested counting universe). Mixing all three in one flat set made "drop level"
  ill-defined ("count across contexts" — to patient? study? global?) and made the empty
  set ambiguous (literal "1 globally" vs. intended "off"). Making the scope implicit —
  always the record's own level — removes both problems: the marginal cross-level
  combinations can no longer be spelled, and `None` is the sole "off" value.
- **Alternatives rejected:**
  - Flat `unique_within ⊆ {parent,user,level}` (prior draft) — the level-as-axis
    conflation above; `()` was a wart special-cased to mean "off".
  - `unique_by=frozenset()` meaning "1 per level" — a third, contradictory spelling of
    `max_records=1` (it even fights `max_records=5`); rejected (see D3, D-empty).
  - A `UniquenessScope` enum — cannot fold `user` in; forces the path validator to
    re-derive the differ-set instead of reading it off `unique_by`.

### D-empty — An empty partition set is rejected, not coerced
- **Choice:** an empty `frozenset` fails config load with a message naming `None` (off) and
  `max_records=1` (one-per-level) as the intended alternatives.
- **Rationale:** with `None` as the off-sentinel and `max_records=1` as one-per-level, an
  empty set is a nonsense middle value; a teaching fail-fast (codebase house style) keeps
  the API discoverable rather than silently normalizing a spelling we just removed.

### D3 — `max_records` stays orthogonal (per-level quota)
- **Choice:** `max_records` unchanged; a separate constraint counted per level context.
  One-record-per-level is spelled `max_records=1` + `unique_by=None`.
- **Rationale:** `first-check` needs `2 per study` (quota) **and** `1 per (study,user)`
  (uniqueness) — two different keys, two different caps, unrepresentable as one
  (key, cap). Parented types get "1 per parent" from `unique_by={"parent"}`'s implicit
  cap-1, not from `max_records` (which they set to `None`, retiring the `max=10000` hack).
- **Alternative rejected:** folding `max_records` into `unique_by` as "cap per bucket"
  — breaks `first-check`.

### D4 — The parent partition is inert when `parent_record_id is None`
- **Choice:** no `"parent" ⟹ parent_required` rule. For a parentless record the `parent`
  partition contributes `None`, so the default `{"user","parent"}` collapses to per-`user`
  uniqueness within the level context, and the validator does not demand `{parent_id}`
  (a null partition can't distinguish two records).
- **Rationale:** this is what makes the default universally safe — it reproduces today's
  per-user default for the parentless majority and only partitions/forces `{parent_id}`
  when `parent_required=True`.
- **Alternative rejected:** validating `"parent" ∈ unique_by ⟹ parent_required=True`
  — would reject every parentless type using the default.

### D5 — Add `{parent_id}` (FK column) AND keep `{origin_type}`
- **Choice:** `fields_from` (`files/_patterns.py`) gains `"parent_id": record.parent_record_id`
  — resolved from the FK column, no parent fetch. `{origin_type}` stays.
- **Rationale:** `{parent_id}` is the **guaranteed** injective discriminator;
  `{origin_type}` resolves to the parent's *type name* (distinguishes types, not two
  same-type parents) and carries a load-dependency wart (`pipeline/context.py:207`
  swallows a failed parent fetch → own type name; `record_service.py:794` resolves with
  parent). Docs state exact-vs-convenient plainly.
- **Alternative rejected:** `{origin_type}` only — insufficient for two same-type parents;
  `{parent_id}` only — throws away the readable option and its existing usages.

### D6 — `parent_record_id` FK → `ON DELETE CASCADE`
- **Choice:** change `ondelete="SET NULL"` → `"CASCADE"` (`record.py:205`).
- **Rationale:** `{parent_id}` must be stable; a null-ed parent shifts the child's own
  file path. The sole application delete path (`record_service.py:834` →`:891`) already
  cascades the subtree, so app behavior is unchanged — CASCADE only affects bypass deletes.
- **Trade-off:** bypass deletes (raw SQL/psql/restore) now remove children silently with
  no `RecordEvent` audit row, instead of orphan-but-recoverable. Coherent-but-gone beats
  recoverable-but-incoherent for a record whose path has shifted. Changelog note required.

### D7 — Path-uniqueness validator at config load, error-by-default with opt-out
- **Choice:** one predicate (below) enforced in both loaders and on `RecordTypeCreate`
  (mirroring `_validate_shared_editing`, `record_type.py:194`). Error by default; explicit
  **per-FileRef** opt-out (`allow_path_collision` on the file binding →
  `RecordTypeFileLink` column + `FileDefinitionRead` field + `FileRef` param) for
  author-known-unique `{data.*}` discriminators — it exempts that FileDef alone, sibling
  FileDefs stay validated. The API PATCH path validates the merged result
  (`RecordTypeService.update` builds the effective `RecordTypeCreate` before persisting),
  so a mutation cannot poison the next startup (in TOML mode a violating PATCH would
  otherwise export to disk and fail the next boot).
- **Rationale:** catches both the silent parent-collision and the self-announcing
  `unique_by=None`-with-`max>1` case (the latter's non-obvious symptom is a
  per-record-checksum invalidation cascade that reads as workflow flakiness).
- **Carve-out:** `multiple=True` FileDefs exempt — `glob_file_paths` wildcards
  placeholders, so a collection pattern is a directory filter, not an identity.

**Predicate.** A pattern containing `{id}` always passes (`record.id` is globally unique).
Otherwise, for each RecordType with a **singular** OUTPUT FileDef, the output path (dir
from `FileDef.level` ∪ filename placeholders) must carry:
- a discriminator for every partition in `unique_by` — `user`→`{user_id}`,
  `parent`→`{parent_id}`, the parent one required only when `parent_required=True`.
  `{origin_type}` counts **only under the opt-out**: it distinguishes parent *types*, not
  two same-type parents — 7 of the incident's 25 dropped pairs were same-type, so bare
  acceptance would have passed a config that still collides;
- the record's own level UID (`{series_uid}`/`{study_uid}`) when `FileDef.level` is coarser
  than the record's level — unconditional, because level context is always in the
  uniqueness scope and records in distinct contexts coexist;
- `{id}` when `unique_by=None` and `max_records` permits >1 per level context.

### D8 — `unique_per_user` deprecated compat kwarg; canonical is `unique_by`
- **Choice:** `RecordDef` accepts `unique_per_user` (translated + warned): `True →
  {"user"}`, `False → None`. The DB column is replaced. Migrated rows do NOT adopt the
  parent-inclusive default (no silent downstream change); new/unspecified types get the
  default `{"user","parent"}`. An explicit `unique_by` wins over the flag.
- **Rationale:** single source of truth, downstream configs keep working.

### D9 — Storage & reconciler
- **Choice:** store `unique_by` as a nullable JSON column — `null` = `None` (off), else a
  canonical sorted list (e.g. `["parent","user"]`). `server_default` = the canonical
  default `["parent","user"]` (matches the model default, satisfying the
  default-vs-server-default guard); the config reconciler compares canonical form.
- **Rationale:** stable equality for reconciliation; `None` is a meaningful explicit value
  (off) distinct from unspecified (which takes the default). The downstream alembic
  migration backfills existing rows via `CASE` on the old boolean (`True→["user"]`,
  `False→NULL`), overriding the `server_default` for those rows.
- **Table-row caveats (found in review):** rows loaded from the DB skip validation
  (`table=True`), so `RecordType.unique_by` holds the **raw JSON list**, not a frozenset —
  the reconciler must canonicalize BOTH sides via `canonical_unique_by` before comparing
  (naive `==` reports a perpetual diff every startup). An explicit `None` (present in
  `model_fields_set`) is a real target value and must bypass the config-None-vs-default
  skip in `_fields_differ` (`reconciler.py:133`) — otherwise an explicit "off" never
  overwrites a default-valued row. The engine's `_pydantic_json_serializer`
  (`db_manager.py:29`) learns `set`/`frozenset` → sorted list, so a frozenset reaching a
  JSON-column flush can never raise.

### D10 — One enforcement method; unbound user defers to assignment time
- **Choice:** one partition-aware repo method
  `ensure_unique_by(record_type, *, user_id, parent_record_id, patient_id, study_uid,
  series_uid)` behind all four enforcement points: route-level `check_constraints`, the
  `inherit_user_from_parent` re-check (`record_service.py:219`), and the user-assignment
  checks (`_check_unique_per_user`, callers at `record_service.py:331/:368/:464`), the
  latter passing the candidate record's own `parent_record_id`. Binding rule: **the check
  runs only when every selected partition is bound** — `parent` is always bound (`None`
  is a value), `user` is bound iff `user_id is not None`. A creation with `"user"`
  selected and no user is skipped entirely (unassigned claiming pools); the invariant
  among assigned records is enforced at claim/assign, which evaluates the fully-bound
  tuple (assignee, the record's own parent).
- **Rationale:** an unassigned record's tuple is not yet evaluable on the user axis
  (`user_id` is mutable by assignment), so pools keep working — this is today's
  `user_id is not None` gate, generalized. `parent` stays structural: `{"parent"}`-only
  types (the incident's fix) dedupe auto-created children at creation even when
  unassigned, and parent-bound-as-NULL is what collapses the default to per-user for
  parentless types (D4).
- **Alternatives rejected:** binding `parent` even when `user` is unbound — would cap
  unassigned pools of default-typed records at one per parent/context and let an assigned
  same-parent record block a pool record (recreating the swallow shape); binding NULL
  user as a value — a second flow-created unassigned record of a migrated `{"user"}`
  type would 409-and-be-swallowed.

### D11 — Claimable-pool filter over prefetched type-name sets
- **Choice:** `_unique_per_user_violation_filter` (`record_repository.py:326`, five call
  sites: `:545`, `:596`, `:1425`, `:1679`, `:1822`) is rewritten as
  `_unique_by_violation_filter(user_id, user_types, user_parent_types)`. Callers prefetch
  two name lists from `recordtype` in one tiny SELECT: types with `"user"` in `unique_by`,
  split by whether `"parent"` is also selected. Filter shape:
  `NOT(record.user_id IS NULL AND ((type ∈ user_only AND EXISTS(ctx-match)) OR
  (type ∈ user_parent AND EXISTS(ctx-match AND parent NULL-safe = candidate.parent))))`,
  NULL-safe equality spelled portably as `or_(and_(a.is_(None), b.is_(None)), a == b)`.
- **Rationale:** claim eligibility must apply the same predicate as the claim itself (a
  listed record must be claimable); JSON containment in SQL is dialect-specific, while a
  prefetch over the tiny `recordtype` table is portable, index-friendly, and house style.
- **Alternatives rejected:** `LIKE` on the canonical JSON text (format-coupled,
  substring-fragile for future partition names); dialect-branched `jsonb @>` /
  `json_each()` (two SQL paths to maintain); denormalized `unique_on_user` boolean columns
  (reintroduces the redundant representation this change removes).

### D12 — SQLite connections enforce foreign keys
- **Choice:** add `PRAGMA foreign_keys=ON` to the existing `_set_sqlite_pragmas` connect
  hook (`db_manager.py:92`); at startup against SQLite run `PRAGMA foreign_key_check` and
  log violations as WARNING without aborting. No kill-switch setting.
- **Rationale:** PostgreSQL has always enforced these FKs and the test suite has run with
  the pragma ON since #71 — production SQLite is the outlier, not the norm. Git history
  confirms enforcement was never deliberately disabled (the only pragma commit, `49f509c`,
  added WAL journaling — orthogonal to constraints; the scrub-db feature #361 merely
  documented the absence). Enabling also makes two existing reliances real on SQLite:
  `delete_records`' `RecordFileLink` cascade and the D6 parent cascade.
- **Trade-off:** legacy SQLite DBs with dangling rows (from the no-enforcement era) start
  failing writes that reference dead rows; the startup audit gives operators visibility,
  the changelog documents it. No kill-switch: an escape hatch would preserve an
  incoherent mode PG never had.

### D13 — Legacy `unique_per_user` translated at the model boundary
- **Choice:** a `model_validator(mode="before")` on `RecordTypeCreate` **and**
  `RecordTypeOptional` pops `unique_per_user`, translates (`True→{"user"}`,
  `False→None`) unless `unique_by` is present, and emits a `DeprecationWarning`.
  `RecordDef` keeps its ergonomic kwarg delegating to the same mapping helper (in
  `clarinet/models/uniqueness.py`); `_to_record_type_create` (`python_loader.py:209`)
  forwards `unique_by` gated on `model_fields_set` instead of the boolean.
- **Rationale:** TOML mode builds `RecordTypeCreate(**props)` directly
  (`bootstrap.py:388`) and the exporter has written `unique_per_user` into every
  downstream `.toml` (`toml_exporter.py:27`); with Pydantic's default `extra="ignore"`,
  an untranslated legacy key would be **silently dropped**, flipping `False`-declared
  types to the parent-inclusive default. One model-boundary chokepoint also covers legacy
  API clients and old admin-UI PATCH bodies.

### D14 — `False` is the TOML spelling of "off"
- **Choice:** `canonical_unique_by` accepts `False` → `None`; `True` is rejected with a
  teaching message naming `{"user"}` (the legacy behavior) and omission (the default).
  The TOML exporter replaces `unique_per_user` in its field list with `unique_by`,
  writing `false` for `None` and the canonical sorted list otherwise.
- **Rationale:** TOML cannot express null, and absence cannot encode "off" — an absent
  key heals toward the concrete default on reconcile, which would silently re-enable
  uniqueness after one export round-trip. `false` reads naturally and makes migration
  from `unique_per_user = false` mechanical.
- **Alternatives rejected:** a `"none"` string sentinel (same second-spelling cost, less
  natural); `[]`→`None` in the TOML layer only (resurrects the exact spelling D-empty
  rejects).

## Risks / Trade-offs

- [Risk] Replacing the `unique_per_user` column breaks downstream on upgrade if the
  compat translation misfires → Mitigation: translate on every construction path (D13 —
  RecordDef kwarg, TOML docs, API bodies), unit-test all mappings, and the reconciler
  heals a drifted DB row toward canonical on startup (issue #389 pattern).
- [Risk] CASCADE silently deletes children on bypass delete with no audit → Mitigation:
  changelog note; the only app path already cascades; document that raw-SQL deletes lose
  history (they already bypass all auditing).
- [Risk] Nullable JSON column with a non-null `server_default` — `None` rows must be
  written explicitly (Pydantic `None` / migration `NULL`), not fall back to the default →
  Mitigation: the migration UPDATEs `False`-origin rows to `NULL`; guard the
  server-default-matches-model-default metadata test for the new column type.
- [Trade-off] Validator is static, so `{origin_type}`-as-parent-discriminator cannot be
  proven (two same-type parents render one filename — 7/25 incident pairs) → Resolved:
  `{origin_type}` counts only under the per-FileRef opt-out (D7); `{parent_id}` is the
  provable option.
- [Trade-off] The static guarantee covers only what config proves: a
  `parent_required=False` type whose records nevertheless receive parents can coexist
  per-parent with no demanded discriminator → Accepted + documented (D4's inert-parent
  rule is what keeps the default safe for the parentless majority); a runtime warning at
  create-with-parent is a possible future extension.
- [Trade-off] Losing cross-level uniqueness (implicit scope) → Accepted: those were the
  marginal combinations; a future explicit `scope:` field can restore them if needed.

## Migration Plan

Framework-level (this change):
1. Add `unique_by` (nullable JSON column, canonical, `server_default ["parent","user"]`) to
   `RecordTypeBase`; remove `unique_per_user` column; add the model validator (D7), the
   legacy before-validators on `RecordTypeCreate`/`RecordTypeOptional` (D13), the compat
   kwarg (D8) + empty-set rejection (D-empty) + `False`-as-off (D14); extend the engine
   JSON serializer for set/frozenset (D9); `allow_path_collision` on `RecordTypeFileLink`
   / `FileDefinitionRead` / `FileRef` (D7).
2. `{parent_id}` in `fields_from`; FK `ondelete="CASCADE"`; `PRAGMA foreign_keys=ON` +
   startup `foreign_key_check` audit (D12).
3. Partition-aware `ensure_unique_by` behind create/inherit/assign/claim
   (`record_repository.py` + `record_service.py`, D10); pool filter over prefetched
   type-name sets at all five call sites (D11); both config loaders invoke the path
   validator; `_to_record_type_create` forwards `unique_by`; PATCH validates the merged
   result (D7).
4. Reconciler compares canonically, honors explicit `None` (D9); TOML exporter writes
   `unique_by` (`false` for None, D14).
5. Frontend: `unique_by` replaces `unique_per_user` in Gleam models/decoders and the
   record-types edit page control.
6. Update `.claude/rules/*.md` + `clarinet/docs/*` (golden-guard) + CHANGELOG (incl.
   SQLite FK enforcement + pool-type guidance) + examples.

Downstream (out of scope, follow-up): generate the alembic migration (column swap with the
`True→["user"]` / `False→NULL` backfill + FK constraint alter +
`recordtypefilelink.allow_path_collision` — alembic is project-level), then edit
`clarinet_nir_liver` configs (`unique_by={"parent"}`, `{parent_id}` patterns, delete the
`lesion-mapping` callback guard) and run a one-off backfill for the 25 dropped reviews.

Rollback: revert the model change + regenerate the reverse migration; the compat kwarg
means downstream configs need no revert.

## Open Questions

All resolved during the review pass (2026-07-21):

- **Opt-out spelling** — RESOLVED: a per-FileRef `allow_path_collision` marker
  (`RecordTypeFileLink` column, surfaced on `FileDefinitionRead`, set via
  `FileRef(..., allow_path_collision=True)`); exempts that FileDef alone (D7). A
  RecordType-level boolean was rejected because it silences validation for every FileDef
  of the type, including future ones.
- **Redundant over-specification** — RESOLVED: leave un-warned (`max_records=1` +
  `unique_by={"user"}` is harmless).
- **`{parent}`-only on a parentless type** — RESOLVED: leave silent, consistent with the
  inert-parent rule (D4); docs carry the pool-type caveat instead (D10).
