Using NRRD for defect segmentations is fine. Backward compatibility doesn't matter, this is a demo project.
Each master-model label can just be a number, no prefix ("1", "2", "3").
A 255-label limit is fine.

Labels
## Segmentation
"defect", "indeterminate", "cosmetic"
## Master model
"1", "2", "3", ...
This needs immutability, not just stability, guaranteed by validators: if a reviewer renames a zone on save, that save must fail with a validation error, not silently succeed. autolabel may run at init.
The Slicer <-> pipeline label-numbering mismatch is resolved by a conversion function (customizable — here `lambda x: int(x)`) mapping label names to numpy array values.

## Projection
"1", "2", "3", ... == Master model
## Second review
"defect", "indeterminate", "cosmetic", "invisible"

enum["defect", "indeterminate", "cosmetic", "invisible"] must be the label *names*, not just JSON enum variants.

General staging scheme:
auto -- pipeline task
man -- manual, through a record + Slicer validation

segmentation_CT_single(man) --> init_master(auto) --> projection[CT](auto)

segmentation_UT_single(man) --> projection[UT](man) --> compare[segmentation_UT, projection_UT](auto) if FP --> update_master[projection_UT](man) and invalidate all projections

A study goes through the following stages:

# Quality assessment (manual)
Issued automatically when a study lands in the database
Result: quality, modality, reference series

# Anonymization (automatic)
On passing quality assessment

# Defect segmentation [CT, CT with archive, UT, CT-HD, UT-HD, micro-CT] (manual)
Appears for studies that passed quality assessment and were anonymized

The inspector sees the anonymized study in OHIF (only the STUDY the task is bound to, except CT-with-archive which also exposes other CT scans)

The reference series determined during quality assessment is loaded in Slicer. A segmentation is created with empty labels [defect, indeterminate, cosmetic]

Result: NRRD segmentation

# Master model construction (automatic)
Appears after the first CT segmentation

# Master model projection [CT, CT with archive, UT, CT-HD, UT-HD, micro-CT]
# Comparison of projection and segmentation

# Approval of new defects
Appears for studies where **comparison of projection and segmentation** found defects on the segmentation that aren't on the projection.

# Master model update
# Second review
