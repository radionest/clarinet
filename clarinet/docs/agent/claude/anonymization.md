---
paths:
  - "plan/workflows/**"
  - "plan/definitions/**"
---

# Anonymization

The framework owns anonymization end to end: it fetches the study from PACS,
anonymizes DICOM in memory, and distributes the result (disk and/or C-STORE back
to PACS). A project does exactly three things:

1. declare the `anonymize-study` RecordDef,
2. wire a flow that fires the built-in task,
3. *optionally* wrap the task to add project-specific fields to the Record.

Never call `AnonymizationService` / `AnonymizationOrchestrator` directly from
`plan/` — go through the built-in task or `run_anonymization`.

## The `anonymize-study` record

Anonymization is tracked by a Record whose type name comes from
`settings.anon_record_type_name` (default `"anonymize-study"`). Declare it in
`plan/definitions/record_types.py`:

```python
anonymize_study = RecordDef(
    name="anonymize-study",     # must match settings.anon_record_type_name
    description="Automatic study anonymization — fetch from PACS, anonymize, distribute",
    label="Anonymize study",
    level="STUDY",
    role="auto",                # machine-driven, no doctor UI
    min_records=1,
    max_records=1,
)
```

`POST /api/dicom/studies/{uid}/anonymize?background=true` resolves the tracking
Record by this name and returns 404 when no such **Record** exists for the study.
Declaring the RecordDef is necessary but not sufficient — a flow still has to
create the Record, so debug that 404 by looking at the flow, not `record_types.py`.

## Wiring the flow

```python
from clarinet.services.dicom.pipeline import anonymize_study_pipeline

# first-check passed → queue anonymization
(record("first-check").on_finished().if_record(F.is_good == True).create_record("anonymize-study"))

# run it as soon as the record appears
(
    record("anonymize-study")
    .on_status("pending")
    .do_task(anonymize_study_pipeline, send_to_pacs=True)
)

# continue once anonymization finishes — fires on the skip branch too (both land `finished`)
(record("anonymize-study").on_finished().create_record("segment-ct-single"))
```

`anonymize_study_pipeline` runs on `settings.dicom_queue_name` and needs
`msg.record_id`. The worker must have DICOM enabled (`have_dicom = true`, or
`clarinet worker --dicom ...`) or nothing drains that queue.

Keyword arguments passed to `.do_task(...)` land in `msg.payload`. Recognised
knobs, each falling back to the matching `settings.anon_*` default:

| Payload key | Falls back to |
|---|---|
| `save_to_disk` | `settings.anon_save_to_disk` |
| `send_to_pacs` | `settings.anon_send_to_pacs` |
| `per_study_patient_id` | `settings.anon_per_study_patient_id` |

## Adding project fields — `run_anonymization`

To attach project knowledge (e.g. `study_type`) to the Record, write a thin task
that delegates to `run_anonymization` and passes `extra_record_data`. The extra
fields are merged into the Record `data` on **all** branches (success, skip, error):

```python
from clarinet.services.dicom.pipeline import run_anonymization

@pipeline_task(queue=settings.dicom_queue_name)
async def anonymize_study_with_type(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Anonymize the study and tag the Record with the project's study_type."""
    first_checks = await ctx.records.find("first-check", study_uid=msg.study_uid)
    study_type = (first_checks[0].data or {}).get("study_type") if first_checks else None
    await run_anonymization(msg, ctx, extra_record_data={"study_type": study_type})
```

`RecordRead.data` is `RecordData | None`, and records created through the API or a
flow's `create_record` land with `data = NULL` — so guard it (`(rec.data or {})`)
rather than calling `.get()` straight on `.data`.

`run_anonymization` reuses `ctx.client` (no extra HTTP connection) and raises
`ValueError` when `msg.record_id` is None — it is record-aware by contract and
will not silently degrade into an untracked run.

**Your wrapper only runs when a flow dispatches it.** The HTTP endpoint above
dispatches the *built-in* directly, so a study anonymized that way gets no
`extra_record_data` — its Record carries the framework fields only. If you also
expose the endpoint, don't assume your project fields are always present (a flow
matching `F.study_type` simply won't fire for those studies).

> **Never name your wrapper `anonymize_study_pipeline`.** Task names are
> `{settings.pipeline_task_namespace}:{function_name}` — the bare function name,
> *not* module-qualified. A project task sharing a built-in's function name
> registers under the same key, and `register_task()` rejects it with
> `PipelineConfigError`. Pick a distinct name
> (`anonymize_study_with_type`, `anonymize_and_tag`, ...). The same rule applies
> to every built-in — see `workflows.md` § Built-in tasks.

## What lands in `record.data`

These are the fields your flows match on (`F.study_type`, `F.skipped`, ...).

| Branch | Fields | Record status |
|---|---|---|
| Success | `anon_study_uid`, `instances_anonymized`, `instances_failed`, `instances_send_failed`, `sent_to_pacs`, `series_count`, `series_anonymized`, `series_skipped` | `finished` |
| Skipped | `skipped: true`, `anon_study_uid` | `finished` |
| Error | `error` (the exception message) | `failed` |

Plus whatever you passed as `extra_record_data`. On error the orchestrator marks
the Record `failed` and **re-raises**, so retry/DLQ middleware still see the
original exception.

## Skip-guard & re-runs

The task is idempotent — safe to re-fire. It skips (writing `skipped: true`) when
**all** of the following hold:

- `study.anon_uid` is already set, **and**
- the previous Record data carries no `error`, **and**
- this run is not upgrading to PACS (either it already sent, or it isn't sending now).

So a re-run *is* performed after a previous failure, or when a run that only saved
to disk is repeated with `send_to_pacs=True`. On the skip branch the **returned**
`AnonymizationResult.anon_patient_id` is `None` — no fresh id is computed for work
already done. That is a field of the return value; it never appears in `record.data`.

If the failure ratio reaches `settings.anon_failure_threshold`, the whole run
raises `AnonymizationFailedError` instead of reporting partial success.

## Patient prerequisite

`Patient.anon_id` is a **computed** property — `f"{anon_id_prefix}_{auto_id}"`, or
`None` when the patient has no `auto_id`. In the default per-patient mode a study
whose patient lacks `auto_id` fails with `AnonymizationFailedError`.

`Patient.anon_name` is assigned by the orchestrator via `anonymize_patient` before
DICOM work starts; it is idempotent (a 409 "already anonymized" is swallowed, which
is what makes concurrent workers on the same patient safe). Names are drawn from
`settings.anon_names_list` when configured.

With `per_study_patient_id=True` the PatientID/PatientName become a per-study
hash instead — `{anon_id_prefix}_{sha256(salt:study_uid) truncated to
anon_per_study_patient_id_hex_length}`, e.g. `CLARINET_a3f5c2e1`. It differs across
studies of the same patient, which prevents PACS-side correlation, and needs no
`auto_id`.

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `anon_record_type_name` | `"anonymize-study"` | RecordType tracking anonymization |
| `anon_uid_salt` | `"clarinet-anon-salt-change-in-production"` | Salt for UID/patient hashing — **change in production** |
| `anon_id_prefix` | `"CLARINET"` | Prefix in `anon_id` **and** in the per-study hash; `[A-Za-z0-9_-]+`, max 55 chars |
| `anon_save_to_disk` | `true` | Write anonymized DICOM under the series working dir |
| `anon_send_to_pacs` | `false` | C-STORE the anonymized study back to PACS |
| `anon_failure_threshold` | `0.5` | Failure ratio at which the run raises instead of reporting |
| `anon_per_study_patient_id` | `false` | Per-study hashed PatientID instead of `anon_id` |
| `anon_per_study_patient_id_hex_length` | `8` | Hex slice length for that hash |
| `anon_names_list` | `None` | Path to a names list for `anon_name` |
| `disk_path_template` | `{anon_patient_id}/{anon_study_uid}/{anon_series_uid}` | On-disk layout |

Changing `anon_uid_salt`, `anon_id_prefix`, `anon_per_study_patient_id` (or its hex
length) after studies exist re-renders every path — already-anonymized data becomes
unreachable. `clarinet anon migrate-paths` only handles `disk_path_template` changes.

## Paths and `AnonPathError`

Anonymized DICOM is written to `dcm_anon/` **inside the series working folder**
resolved from `disk_path_template`.

`ctx.files` is strict: for a not-yet-anonymized record it raises `AnonPathError` at
construction time rather than falling back to raw UIDs. This is deliberate — a study
can be anonymized mid-pipeline. See `workflows.md` § `ctx.files` for the full
contract and the lenient `Files(record, fallback=True)` opt-out.

## Operator CLI

```bash
# Relocate anonymized data after a disk_path_template change
uv run clarinet anon migrate-paths \
    --from '{anon_patient_id}/{anon_study_uid}/{anon_series_uid}' \
    --to   '{anon_patient_id}/{study_date}/{anon_series_uid}' \
    --dry-run                    # preview; add --cleanup-empty to prune empty parents
                                 # --include-working-folder moves pipeline outputs too

# Turn a restored production copy into a shareable test fixture
uv run clarinet anon scrub-db --patients P001,P002              # scrub + audit + commit
uv run clarinet anon scrub-db --patients all --dry-run          # rehearse, then roll back
uv run clarinet anon scrub-db --patients all --out dump.sql.gz  # commit, then pg_dump
```

`--out` is ignored under `--dry-run` (and after an audit failure): the dump is gated
on the scrub actually committing, so the two flags do not combine.

`scrub-db` operates **in place** on the database in `settings.database_*` — restore a
production copy into a throwaway scratch DB *before* running it. It strips PHI from
relational columns, `record.data`, and audit JSON snapshots, rewrites the patient MRN
to the deterministic `anon_id`, and audits for surviving PHI (refusing to commit on a
hit unless `--allow-phi-leak`). `study.anon_uid` / `series.anon_uid` / `patient.auto_id`
are preserved, so `Files` still resolves the anonymized DICOM on disk.

## Details

- `{{CLARINET_DOCS}}/pipeline-ops.md` — queues, retries/DLQ, built-in task reference
- `workflows.md` — `@pipeline_task`, `TaskContext`, RecordFlow DSL
