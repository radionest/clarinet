Comparative diagnostic-effectiveness study of NDT imaging modalities for internal-defect detection in cast parts.

## Study plan

When a part meets the inclusion criteria, it is scheduled for a repair operation. Shortly before the repair operation, the following scans are performed on it:
- CT with standard settings (CT)
- Ultrasonic volumetric scan (UT)
- High-resolution/enhanced CT (CT-HD)
- High-resolution UT (UT-HD)
- Micro-CT (MCT)

The part may also have a baseline archive — prior CT scans of the same part, taken before an earlier repair/rework cycle. There may be several such archive studies. Segmentation is not performed directly on the baseline archive, but this data is available to the inspector during segmentation with extended context (`segment-ct-with-archive`).

Each modality is represented by **one** study (except the baseline archive). CT segmentation is done in two variants: as an isolated series, and together with the baseline archive.

## Study stages

### 1. Quality assessment (first-check, level: STUDY)

Performed by inspectors as two independent assessments (min_records=2, max_records=2). Determines:
- `is_good` — whether the study is usable
- `study_type` — modality type (CT, UT, CT-HD, UT-HD, MCT, CT-archive)
- `best_series` — UID of the reference series with the fewest artifacts and the most reference landmarks

Defects are searched across all available series, but marking is only done on the reference series.

### 2. Anonymization (anonymize-study, level: STUDY, role: auto)

Runs automatically once the study passes quality assessment. All part-identifying data is replaced with anonymous identifiers. Result: `anon_study_uid`, plus instance/series statistics.

### 3. Defect segmentation (level: STUDY)

A separate RecordType per modality, gated by role:

| RecordType | role | Description |
|---|---|---|
| segment-ct-single | inspector_CT | CT, only the current study |
| segment-ct-with-archive | inspector_CT | CT + baseline archive studies via `lifecycle.open` |
| segment-ut-single | inspector_UT | UT, only the current study |
| segment-ut-hd-single | inspector_UT | High-resolution UT (same role as UT — there is no separate `inspector_UT-HD` role) |
| segment-ct-hd-single | inspector_CT-HD | High-resolution/enhanced CT |
| segment-mct-single | inspector_MCT | Micro-CT |

All types: min_records=2, max_records=4. The flow creates `min_records` records on trigger. Each inspector creates their own file `segmentation_{origin_type}_{user_id}.seg.nrrd`.

The inspector views the anonymized study in the OHIF viewer, loads the reference series in 3D Slicer, and marks defects in three categories: defect, indeterminate, cosmetic.

Assessment of CT with archive data always happens after the assessment of the isolated study. If an inspector is assigned to assess several modalities, the modalities are presented in order from the least sensitive method to the most sensitive: CT, UT, CT-HD, UT-HD, MCT, CT with archive. At least 2 days must pass between one inspector's assessments of different modalities, or the inspector must have assessed at least 10 other studies in between.

The `_with_archive` variant (extended viewing context) is architecturally supported for any modality via the `lifecycle.open` mechanism, but is currently only implemented for CT.

### 4. Initial master model construction (pipeline task)

Runs automatically after the first completed CT segmentation with archive data (`segment-ct-with-archive`). The `init_master_model` task fires on every completion and checks for the file itself: `if ctx.files.exists(master_model): return`.

The coordinate grid for the model is defined by the reference CT series. Each defect in the segmentation is binarized and split into connected components with unique numbers.

### 5. Master model projection (create-master-projection, level: SERIES)

Manual creation of the master model's projection onto a specific series' coordinate space. role=expert, min_records=1, max_records=1. Input file: master_model (role=input). If the master model file is missing, the record gets status `blocked`.

For CT studies the projection is created automatically (a copy of the master model — same coordinate system). For non-CT modalities the expert manually places ROIs from the master model into the target series' coordinate space.

On save, the projection is validated: its segment names must exactly match the master model's segment names (i.e. every defect from the master model is represented, and no extra ones are added).

### 6. Comparing projection and segmentation (compare-with-projection, level: SERIES, role: auto)

Automatic comparison of the inspector's segmentation with the master model projection. For each ROI, the pipeline checks for overlap: overlap present → same defect; no overlap → false negative or false positive. Created and filled by the pipeline without human involvement. Linked to a specific segmentation record via `parent_record_id`.

The comparison runs **for each** segmentation on a given series (if 4 inspectors segmented it, 4 comparisons are created).

Defects are classified as:
- **detected** — overlap between the projection ROI and the segmentation ROI
- **missed** (false negative) — defect on the projection but not on the segmentation → second-review
- **additionally detected** (false positive) — defect on the segmentation but not on the projection → update-master-model

### 7. Second review (second-review, level: SERIES)

One generic type covering all modalities. Created for the specific inspector whose segmentation diverges from the master model projection. Linked to the segmentation record via `parent_record_id`.

The inspector gets the reference series in 3D Slicer with the defects from the projection that they missed (each under its own number). Their task is to assign each defect to one of four categories: defect, indeterminate, cosmetic, invisible. The inspector doesn't know which modality the defect was originally found on.

The second-review stage separates two qualitatively different phenomena at the per-defect level: **method limitation** (the defect isn't visible on this modality due to insufficient contrast resolution, size, location, or artifacts) and **observer error** (the defect is visible but was missed). The "invisible" category indicates a method limitation, while "defect", "indeterminate", or "cosmetic" indicate a missed-but-visible defect.

Preparation script:
1. Takes master_projection and subtracts the ROIs the inspector already marked during their initial segmentation
2. If a second-review was previously performed and invalidated, also subtracts the ROIs from the previous second-review (on save, the previous and new segmentation are merged)
3. Creates two layers:
   - **master_projection**: each remaining defect under its own number
   - **output**: 4 empty regions of interest — defect, indeterminate, cosmetic, invisible
4. The inspector uses the "add island" tool to reassign defects from the first layer to the second

Invalidated if a repeat comparison finds **additional** false negatives.

### 8. Retrospective characterization (retrospective-characterization, level: SERIES)

After all defect-detection stages are complete (segmentation, comparison, second review), a retrospective assessment of each defect's signal characteristics is performed on every modality. Carried out after a blind-reassessment interval (4–7 weeks). level: SERIES, role: auto, min_records=2, max_records=4. No Slicer script — assessed via the OHIF viewer with form-based data entry.

The inspector records per-defect characterization (signal pattern, texture, morphology, edge definition, etc.) on the reference series of the corresponding modality. Works with the anonymized study and the master model projection, on which all defects are marked.

Created manually by the coordinator — not an automatic pipeline trigger.

### 9. Master model update (update-master-model, level: PATIENT)

Manual update of the master model by the expert. role=expert, max_records=1. Created automatically by the pipeline when a false positive is found in compare-with-projection (and also when additional defects are found during the repair operation, stage 12).

In 3D Slicer, the expert is given the master model with the reference CT series, plus the projection of the model onto the modality with the additionally detected defects (highlighted in a separate color). The expert transfers the additional defects from the projection onto the master model. Numbering is assigned automatically.

Only one update task can be active at a time. After the update, all projections are invalidated and redone. The master model's coordinate grid does not change. Updates never remove existing defects, only add new ones. Defect numbers are immutable — on save, it's validated that previously present defects have not changed number.

### 10. MRB conclusion (mrb-conclusion, level: PATIENT)

The Material Review Board (MRB) — composed of the CT inspector, UT inspector, and repair technicians — classifies all defects. role=mrb, min_records=1, max_records=1. Input file: master_model (input).

Classification into six categories: defect, resolved_defect, indeterminate, cavity, inclusion, cosmetic_indeterminate. A repair tactic is also assigned: cluster_repair, isolated_repair, or not_planned.

### 11. Repair planning

#### 11a. 3D repair model (repair-model, level: PATIENT)

role=expert, min_records=1, max_records=1. Input file: master_model (input), output: repair_model_file (output).

The part body is segmented via automated thresholding with manual boundary correction. Internal channels are segmented with the threshold-mask tool, split into primary and secondary channel networks. Defect ROI boundaries are corrected to their true extents. Defects not resolvable on CT (including resolved defects) are marked with a 5mm-diameter spherical ROI.

#### 11b. Repair plan (repair-plan, level: PATIENT)

role=expert, min_records=1, max_records=1. Input files: repair_model_file, master_model.

The repair is planned on the 3D model. It's determined which defects will be repaired together as a cluster and which individually. Repair zones are outlined with the "scissors" tool. Residual material volume is calculated.

#### 11c. Repair report (repair-report, level: PATIENT)

role=technician, min_records=1, max_records=1. Input file: master_model (input). Automatically prefilled from repair-plan (defects with their planned clusters).

The technician confirms or adjusts the cluster assignment per defect, and can record additional defects (with a description and cluster) found beyond the master model.

### 12. Repair operation (repair-protocol, level: PATIENT)

role=technician, min_records=1, max_records=1. Input file: master_model (input). Takes place a few days after the CT-HD scan.

In-process UT is used to search for all defects marked on the model, marking their projection onto the part surface. Each defect is classified as found, not found, or additionally found. Removed fragments are numbered, with a record of which defects are located in each fragment.

If additional defects are found, `update-master-model` is created automatically.

### 13. Post-repair CT (post-repair-ct-review, level: STUDY)

Performed to screen for post-repair anomalies. role=inspector_CT, min_records=1, max_records=2. If defects were found and removed during the repair operation that weren't identified during the detection phase, the post-repair CT is used to update the master model.

### 14. Metallography (metallography, level: PATIENT)

role=analyst, min_records=1, max_records=1. Input file: master_model (input).

Sectioning analysis is performed by a panel of the analyst, CT inspector, and repair technician. The analyst is told the count, size, and approximate location of defects in the removed fragment. Correspondence between sectioned defects and master-model defects is logged. If a defect isn't found on sectioning, a sample is taken from the approximately corresponding location. Microscopy determines, per defect: presence of defect material (yes/no/no_data), ratio of defect-to-sound material.

## Master model

The master model is a segmentation where each defect occupies a separate ROI with a unique number.

Its basis is the part's first CT with a completed `segment-ct-with-archive` record (the first one completed, across however many inspectors performed it). The series is chosen from the `best_series` field of the first-check record. Once created, the basis of the master model does not change.

The first projection (onto the same series as the basis) is a copy of the master model.

### Race condition: master model update during projection creation

The projection record stores a hash of the master model it was created from. The check is event-driven: on projection completion (finish), the stored hash is compared against the current master model file. On mismatch, the projection is invalidated. No work is lost — the check only happens at finish time.

## Multiple users

RecordType parameters:
- **min_records** — the minimum number of records created when the flow fires. Determines only the initial count, doesn't affect progression through the flow
- **max_records** — the maximum number of records of this type per entity (the UID depends on level). Trying to create more raises an error

Progression through the flow is governed by the flow's own conditions, not by min/max_records.

Each record is independent: it has its own lifecycle, its own segmentation file, its own `finished` status. Different inspectors' segmentations do not block each other.

When each individual segmentation finishes, the full cycle runs: projection creation, automatic comparison, second-review for that specific inspector on any discrepancy, update-master-model on any false positive.

## Roles

- **inspector_CT, inspector_UT, inspector_MCT, inspector_CT-HD** — assignment filter. Only an inspector with the matching role can be assigned a task of that type
- **inspector** — generic (modality-agnostic) inspector role, used for tasks that aren't tied to a specific modality (first-check, second-review, view-nifti)
- **expert** — role for updating the master model and creating projections
- **mrb** — role for Material Review Board participants
- **technician** — role for the repair technician (repair-stage records)
- **analyst** — role for the materials analyst (metallography)
- **auto** — record is created and filled by the pipeline. No user_id

All roles (except auto) are configured via `extra_roles` in settings.toml.

## Record relationships

Implementation: `parent_record_id` (FK on Record) — a one-to-many relationship. One parent record can have several child records (e.g. segmentation -> compare, second-review).

The projection does not store an explicit reference to the segmentation — the relationship is defined via the shared `series_uid` (projection level=SERIES, max_records=1 — one projection per series).

Example relationships:
- compare-with-projection.parent_record_id -> segmentation (which segmentation is being compared; the projection is found by series_uid)
- second-review.parent_record_id -> segmentation (which inspector's review this is)
- update-master-model.parent_record_id -> compare-with-projection (which comparison triggered this update)

## Blocking and execution order

Segmentations **do not depend** on the master model and run in parallel.

Records that depend on the master model (create-master-projection) get status `blocked` when the input master_model file is missing. They unblock automatically once the file appears.

Studies can arrive in batches. The order in which segmentations finish is not guaranteed. The master model is created from the first completed CT segmentation with archive data (from any of the inspectors).

## File levels and FileAccessor

The file level determines:
1. **Where it's stored** — the part's folder (PATIENT), study folder (STUDY), or series folder (SERIES)
2. **Coordinate guarantees** — files at the same level on the same entity share the same coordinate grid (pixel count, pixel spacing, coordinate origin)
3. **Duplicate protection** — e.g. master_model (level=PATIENT) is unique per part

Files at different levels have no such coordinate guarantees.

## Example sequence

The process starts with CT: first-check runs (2 independent assessments), determining study_type=CT and best_series. After quality assessment passes, anonymization runs automatically. Once anonymization finishes, segment-ct-single and segment-ct-with-archive records are created (min_records=2 for each type).

An inspector completes segment-ct-with-archive (the first of several). This is the first CT segmentation with archive data → the master model is created automatically (init_master_model). The projection of the master model onto this same series is a copy of the master model (auto_project_ct).

A UT scan is performed → first-check → anonymize-study → segment-ut-single. UT segmentation does not depend on the master model and runs in parallel. A create-master-projection task is created for the UT series. If the master model exists, the expert marks the projection. If not, the record stays `blocked`.

Automatic comparison of projection vs. segmentation runs for each inspector. Result: one defect on the UT segmentation is absent from the projection → the pipeline creates update-master-model. The expert adds the new ROI. The master model hash changed → all projections are invalidated → re-marking is required. Second-review is created for inspectors whose segmentations had discrepancies.

Subsequent modalities (CT-HD, MCT) go through the same cycle. Once all defect-detection stages are complete, after 4–7 weeks, inspectors retrospectively record each defect's signal characterization.

Next: the MRB classifies all defects → repair-model → repair-plan → repair-report (prefilled from the plan) → repair-protocol (repair operation) → update-master-model on additional findings → post-repair-ct-review → metallography.

## lifecycle.open — contract

OHIF natively supports loading multiple studies via a repeated query parameter:
```
/ohif/viewer?StudyInstanceUIDs=1.2.3&StudyInstanceUIDs=1.2.4&StudyInstanceUIDs=1.2.5
```

The current implementation (`src/frontend/src/utils/viewer.gleam`) builds a URL with a single study_uid. The record-execution page (`records/execute.gleam`) already takes the record's level into account when building the viewer button.

### Contract

`lifecycle_open` is a RecordType field referencing a Python script. The script is a pure function that receives the record's context (patient_id, study_uid, series_uid) and returns a list of additional study UIDs to load in the viewer.

```python
# add_previous_ct_studies_to_viewer.py
async def lifecycle_open(ctx: RecordContext) -> list[str]:
    """Return additional study UIDs to load in OHIF viewer."""
    archive_studies = await ctx.client.find_studies(
        patient_id=ctx.patient_id,
        study_type="baseline archive",
    )
    return [s.study_uid for s in archive_studies]
```

### Implementation in clarinet

1. **API**: `RecordRead` gets a computed field `viewer_study_uids: list[str]`. Default — `[record.study_uid]`. If the RecordType has `lifecycle_open` set, the script is invoked and its result is appended to the list.
2. **Frontend**: `viewer_url()` takes a `list[str]` instead of a single `study_uid`, building a URL with multiple `StudyInstanceUIDs`.
3. **Caching**: the result of `lifecycle_open` can be cached at the record level (study_uids only change when new studies are added to the part).

A separate endpoint isn't needed — it's enough to include `viewer_study_uids` in the existing `GET /api/records/{id}` response.
