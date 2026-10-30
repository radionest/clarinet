<!--
Raw capture of the interactive brainstorming (opsx:explore → opsx:propose).
Decision-log format: background → decision chain → trade-offs.
Framework = clarinet; the triggering bug lives in downstream clarinet_nir_liver.
-->

# Brainstorm — RecordType cardinality constraints (`unique_within`)

## Background — the triggering bug (downstream, out of scope to fix here)

In `clarinet_nir_liver`, 25 of 198 (doctor, study) pairs (12.6%, 21 patients) have
the same doctor holding two segmentations on one study. Both segmentations share
`best_series`, both spawn `compare-with-projection`, and both try to spawn a
`second-review` for the same (doctor, series). `second-review` is
`unique_per_user=True`, so the second creation 409s with `UNIQUE_PER_USER`.

The 409 is swallowed: `second-review` is created from a `.call()` callback
(`plan/workflows/callbacks.py:395`), so it lands in the RecordFlow engine's
`call_function` handler except at `clarinet/services/recordflow/action_handlers.py:236`
(NOT `create_record` at :145 — corrected during exploration), where
`is_expected_conflict()` downgrades it to a silent `logger.warning`. Effect: for
those 25 combos the doctor gets one second-review covering whichever parent won the
race; the other segmentation's missed lesions are never reviewed (~233 FN lesions).

Root cause is a **counting-axis mismatch**: `unique_per_user` counts one record per
(series, user), but `second-review`'s true identity is one record per **parent
segmentation**. The dominant combo (18/25) is `segment-ct-single` +
`segment-ct-with-archive` — same doctor, because `maybe_create_archive_segment_for_doctor`
auto-creates the archive segmentation for that doctor.

## Rule of three — the primitive is missing, not just one type is misconfigured

Three nir_liver RecordTypes each want "scope = parent record" and each invented a
different workaround (their own comments are the evidence):

| RecordType | wants | declared | workaround | leak |
|---|---|---|---|---|
| `compare-with-projection` | 1 per parent segmentation | SERIES, `max_records=10000` | surrender the cap | no cap at all |
| `second-review` | 1 per parent segmentation | SERIES, `max=10000` + `unique_per_user` | count on the wrong axis | **25/198 silently dropped** |
| `lesion-mapping` | 1 per source record | SERIES, `max=20` + callback guard | hand-rolled `any(lm.parent_record_id == record.id)` in a callback | 20 is a guess; extra round-trip; races |

`lesion-mapping`'s callback guard is literally a userspace reimplementation of
"max_records=1 per parent". The framework is missing a per-parent cardinality axis.

## The two entangled halves

1. **Constraint axis** — the equivalence class `max_records` / uniqueness counts over.
2. **Path identity** — which records resolve to the same output file
   (`FileDef.level` × placeholders in the pattern).

`unique_per_user=True` was *accidentally* holding the path invariant: "one record per
(series, user)" happened to exactly match "one file per (series, user)" because the
pattern is `second_review_{user_id}.seg.nrrd`. Fix the counting axis without fixing
the pattern and you trade 25 missing reviews for 25 pairs of reviews **clobbering one
`.seg.nrrd`** — worse, because the current failure is at least countable. So both
halves must land together.

## Decision chain

### Q1 — Where does the per-parent counting axis live? → NOT on `level`

Initial idea (user): add a new `RecordType.level` value `"RECORD"` that counts over
record→record links. Rejected during exploration:

- `level` answers **placement** (required UIDs, working dir, viewer context, anon path
  segment, RecordFlow tree filter) — 6 of 8 `DicomQueryLevel` consumers would need a
  parent dereference; a `RECORD` member has no working-dir answer of its own
  (`clarinet/files/facade.py:53` keys `_dirs` by PATIENT/STUDY/SERIES).
- `parent_record_id` is `ON DELETE SET NULL`, so deriving *level* from parent makes a
  nullable FK load-bearing: delete the parent and the child's level is undefined.

**Decision:** keep `level` as pure DICOM placement; add an **orthogonal** field for the
counting axis. `check_constraints` already receives `parent_record_id`
(`record_repository.py:1205`, passed at `record.py:343` for `parent_required`) — it just
never counts on it.

### Q1′ — Enum vs. set of axes → `unique_within: Sequence[Literal["parent","user","level"]]`

First proposed a `UniquenessScope` enum (`LEVEL` / `PARENT` / `PARENT_AND_LEVEL`). User
countered: make it an **order-insensitive sequence of axis literals** including `user`,
default `("parent","user","level")`. This is better because the **path-validator
predicate collapses to "the pattern must discriminate every axis in `unique_within`"** —
`unique_within` *is* the guaranteed-differ set, so constraint side and file side read
off the same field instead of one being derived from the other.

Semantics: **at most one record per distinct tuple of the selected axes' values.**
This subsumes `unique_per_user` (→ `"user" ∈ unique_within`) and adds `parent`, `level`.

Rejected the list-of-axes-with-`user` idea earlier for fear of two ways to spell
`unique_per_user`; user explicitly chose to fold `user` in and deprecate the boolean.

### Q2 — `max_records` folds into `unique_within`? → NO, orthogonal (per-level quota)

User's original mental model conflated `max_records` with the per-parent count. But
`first-check` proves quota and uniqueness are two different keys with two different caps:

```
first-check: max_records=2, (default uniqueness), level=STUDY, NO output file
  want "2 doctors per study, 1 each"
     = 2 per (study)         ← quota,      cap 2, key {level}
     + 1 per (study, user)   ← uniqueness, cap 1, key {user, level}
```

No single (key, cap) expresses that. **Decision:** `max_records` stays exactly as today
— a quota counted per **level context**, orthogonal to `unique_within`. Parented types
get "1 per parent" from `unique_within=("parent", …)`'s implicit cap of 1, NOT from
`max_records` — which is what retires the `max=10000` workarounds (they set
`max_records=None`). A configurable key for `max_records` (N>1 per parent) is a future
extension; nothing in the domain needs it.

### Q2′ — The parentless-default collapse (what makes the all-three default safe)

For a parentless type, `parent_record_id` is `None` on every record, so
`unique_within=("parent","user","level")` collapses:

```
1 per (None, user, level) ≡ 1 per (user, level) ≡ today's unique_per_user=True
```

The default reproduces today's default for parentless types (the majority) and the
validator does NOT demand `{parent_id}` for them (two parentless records can't differ on
a null axis). The `parent` axis only partitions — and only forces a `{parent_id}`
discriminator — when `parent_required=True`. No `"parent" ⟹ parent_required` rule; the
axis just goes quiet. This is the load-bearing insight that makes the chosen default
universally safe AND the fix for `second-review` the moment it declares a parent.

### Q3 — `{parent_id}` vs `{origin_type}` file placeholder → keep BOTH

`{origin_type}` already exists (`clarinet/files/_patterns.py:38`) but resolves to the
parent's **record type name**, not identity — so it distinguishes
`segment-ct-single` from `segment-ct-with-archive` (handles the 18/25 case) but NOT two
parents of the *same* type. It also has a load-dependency wart: `pipeline/context.py:207`
swallows a failed parent fetch → `resolve_origin_type` returns the record's OWN type
name, while `record_service.py:794` resolves it WITH the parent → same record can render
two filenames depending on subsystem + transient fetch success.

**Decision:** add `{parent_id}` resolved from the FK column `record.parent_record_id`
(total, load-independent, no fetch) as the **guaranteed** discriminator; keep
`{origin_type}` as the **readable** option that is sufficient only when parents of a given
child type always differ in record type. Docs must state exact-vs-convenient plainly.

### Q4 — `{parent_id}` vs `ON DELETE SET NULL` → change FK to CASCADE

`parent_required=True` guards creation, not deletion. If a parent is deleted, the child's
`parent_record_id` → NULL and `{parent_id}` renders differently → the record loses track
of its own file. There is exactly ONE application delete path
(`record_service.py:834` `collect_descendants(for_update=True)` → `:891` `delete_records`)
and it already deletes the whole subtree. `SET NULL` only ever fires for deletions that
bypass `RecordService` (raw SQL / psql / restore).

**Decision:** flip the FK to `ON DELETE CASCADE`. This does not change app behavior (the
service already cascades); it makes the schema agree with the service and closes the
orphan state. Accepted trade: a bypass delete now removes children **silently, with no
`RecordEvent` audit row** (the app-level audit write is what's bypassed), instead of
leaving them orphaned-but-recoverable. Coherent-but-gone beats recoverable-but-incoherent
for a record whose `{parent_id}` path has shifted. Needs a changelog note + a constraint
migration (downstream generates the alembic migration; clarinet is framework-level).

### Q5 — Field name → `unique_within`

Considered `scope` (symmetry with `level`), `count_within`, `branch`. User chose
**`unique_within`** — self-documenting, reads as English, names uniqueness directly.

### Q6 — `max_records=N>1` with no discriminator deserves a check too → YES, same predicate

Empirical scan of all 55 RecordDefs (nir_liver 28, examples/demo 25, template 2): **zero
current violations**, but only because the shipped default pair is `max_records=None`
(unlimited) + `unique_per_user=True`, so every default type is "unlimited, one per user"
and `{user_id}` in the pattern is what makes it safe — the SAME accident that held
`second-review` together. `lesion-mapping` (`max=20`, `unique_per_user=False`, INPUT-only)
is one `FileRef(x,"output")` away from breaking.

Severity is inverted from first framing: the `max>1`-no-discriminator collision fires on
record #2, 100% of the time — **self-announcing**, unlike the silent parent bug. The one
symptom that is NOT self-announcing: each record holds its own `RecordFileLink.checksum`
for the shared path, so when B writes, A's next `check_files` sees a changed checksum and
fires `on_file_change` → an invalidation cascade that reads as "flaky workflow", not
"filename collision". That is the strongest argument for catching it statically.

**Decision:** one predicate covers both cases. Error by default with an **explicit
opt-out** (a designer may know a `{data.FIELD}` placeholder is unique in ways the
framework can't prove — e.g. `report_{data.timepoint}.pdf`). Carve-out: **exempt
`multiple=True` FileDefs** — for collections `glob_file_paths` wildcards placeholders, so
the pattern is a filter over a shared directory, not an identity; a naive check would flag
every legitimate one.

### Q7 — `level` is not an axis; scope is implicit → `unique_by` (partitions only)

Reviewing whether all 8 subsets of `{parent,user,level}` are meaningful exposed that the
three "meaningless-feeling" ones (`{user}`, `{parent}`, `{parent,user}`) all share one
trait: they DROP `level`. Root cause — `level` is a different KIND of thing than
`parent`/`user`: it is a **scope** (a nested counting universe: series ⊂ study ⊂ patient),
while `parent`/`user` are flat **equality partitions**. In a flat set, "drop level" is
underspecified ("count across contexts" — to what? patient? global?), and `()` is a wart
(literal meaning "1 globally", special-cased to mean "off").

**Decision:** make the scope **implicit** — always the record's own level — and reduce the
field to partitions only: **`unique_by: frozenset[Literal["user","parent"]] | None`**,
default `frozenset({"user","parent"})`. Semantics: ≤1 record per distinct combination of
the selected partitions, within the record's level context. `None` = no uniqueness. This
designs the marginal cross-level combinations out of existence (they can't be spelled) and
makes `None` the sole "off" value. Cost: cross-level uniqueness (global-per-user, one child
per parent across contexts) is no longer expressible — accepted; those were the marginal
rows, and a future positive `scope:` field can restore them if ever needed. Renames
`unique_within` → `unique_by` (partitions, not scopes).

### Q8 — `unique_by=frozenset()` is bad API → reject empty; per-level = `max_records=1`

`frozenset()` ("1 per level") would be a THIRD spelling of `max_records=1` and can even
contradict `max_records=5`. **Decision:** an empty set is REJECTED at config load with a
teaching message ("use None for no uniqueness, or max_records=1 for one-per-level"). This
gives one canonical spelling per intent: per-level = `max_records=1` + `unique_by=None`;
per-(level,user) = `unique_by={"user"}`; per-(level,parent) = `unique_by={"parent"}`.

## Consolidated model (final — supersedes the flat `unique_within` sketch below Q6)

- **`unique_by: frozenset[Literal["user","parent"]] | None = frozenset({"user","parent"})`**
  — relationship partitions applied within the record's own level context (scope implicit,
  never a partition). `None` = no uniqueness; empty set REJECTED at config load.
- **`max_records: int | None`** — unchanged; quota per level context, orthogonal. Per-level
  cardinality is spelled `max_records=1` + `unique_by=None`.
- **`unique_per_user`** — deprecated compat kwarg on `RecordDef`, honored + translated +
  warned. Canonical is `unique_by`.
- **`{parent_id}`** placeholder from the FK column; **`{origin_type}`** kept.
- **Path-uniqueness validator** at config load (both loaders + `RecordTypeCreate` model
  validator, mirroring `_validate_shared_editing` at `record_type.py:194`).
- **`parent_record_id` FK** → `ON DELETE CASCADE`.

Canonical spelling per intent (no state means the same thing twice):

| intent | spelling |
|---|---|
| unlimited per level | `unique_by=None`, `max_records=None` |
| N per level | `unique_by=None`, `max_records=N` |
| 1 per level | `unique_by=None`, `max_records=1` |
| 1 per (level, user) | `unique_by={"user"}` |
| 1 per (level, parent) | `unique_by={"parent"}` |
| 1 per (level, user, parent) | `unique_by={"user","parent"}` *(default)* |

Behavior-preserving migration mapping:

| old | → `unique_by` |
|---|---|
| `unique_per_user=True` | `{"user"}` |
| `unique_per_user=False` | `None` |
| unspecified (new types) | default `{"user","parent"}` |

Migrated rows deliberately do NOT adopt the parent-inclusive default → no silent
downstream change; new types opt into parent-awareness via the default.

## Path-uniqueness predicate (final)

A pattern containing `{id}` always passes (`record.id` is globally unique). Otherwise, for
each RecordType with a **singular** OUTPUT FileDef, the output path (dir from `FileDef.level`
∪ filename placeholders) must carry:

- a discriminator for each partition in `unique_by` — `user` → `{user_id}`; `parent` →
  `{parent_id}` (guaranteed) or `{origin_type}` (opt-out; conditionally sufficient),
  required only when `parent_required=True`
- the record's own level UID (`{series_uid}` / `{study_uid}`) when `FileDef.level` is
  **coarser** than the record level — unconditional, since level context is always part of
  the uniqueness scope
- `{id}` when `unique_by=None` and `max_records` permits >1
- `multiple=True` FileDefs exempt

## Scope

Framework (`clarinet`) only. The deliverable is: make the downstream nir_liver fix a
**config edit** (`unique_within=("parent",…)` + a `{parent_id}` pattern, delete the
`lesion-mapping` callback guard) **plus a startup error that names the offending file**.
The nir_liver config changes are an explicit out-of-scope follow-up; the change is judged
on whether it turns that fix into config + a clear fail-fast.

## Storage / reconciler notes (impl detail for design)

- `unique_by` stored as a **nullable** JSON column (PortableJSON): `null` = `None` (off),
  else a canonical sorted list. `server_default '["parent","user"]'` matches the model
  default (additive-migration rule); the reconciler compares canonical `frozenset|None`.
  Downstream alembic backfills via `CASE` on the old boolean (`True→["user"]`, `False→NULL`).
- Doc updates hit `.claude/rules/*.md` AND the byte-identical copies under `clarinet/docs/`
  (golden-guard test fails on drift).
