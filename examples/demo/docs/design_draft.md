# Comparative diagnostic-effectiveness study of NDT imaging modalities for internal-defect detection

## Objective

Evaluate diagnostic effectiveness of different NDT imaging modalities for internal-defect detection.

## Participants

- Lead Inspector (monitor) — 1 CT/UT inspector, 5+ years NDT experience
- Group A — inspectors, 5+ years NDT experience
- Group B — 2 inspectors, 1+ year CT-HD viewing experience
- Group C — repair technicians, 5+ years experience
- Expert — creates/updates the master model

## Modalities

- CT — industrial computed tomography
- UT — ultrasonic volumetric scan
- CT-HD — high-resolution/enhanced CT
- UT-HD — high-resolution UT
- MCT — micro-CT
- Baseline archive — prior CT scans of the same part (pre-dating an earlier repair/rework cycle), several possible

One study per modality (except baseline archive). No direct segmentation on the baseline archive. CT segmentation done both isolated and with-archive.

## Master model

- Reference segmentation, one ROI per defect, unique numbers
- Basis = part's first CT with completed segmentation; coordinate grid fixed from there on
- Extended with new defects as later modalities are reviewed
- Each new modality gets a projection of the master model into its own coordinate space

## Stages

1. **Quality assessment** (manual, Group A, 2 independent reads) — suitability, modality type, reference series
2. **Anonymization** (automatic) — on passing QA
3. **Defect segmentation** (manual, modality-specific inspector role, 2 independent reads) — OHIF + Slicer, labels: defect/indeterminate/cosmetic. Order: CT, UT, CT-HD, UT-HD, MCT, then CT+archive; 2-day gap or 10 other studies between an inspector's modalities
4. **Master model construction** — after first completed CT-with-archive segmentation; connected-components labeling
5. **Master model projection** (manual, expert) — per modality; auto for CT (copy), manual placement (geometric/structural landmarks if not visible) for others; validated names must match master model
6. **Comparison** (automatic) — per-ROI overlap: match / missed (→ second review) / additional (→ master model update); run per segmentation
7. **Second review** — inspector classifies missed defects: defect / indeterminate / cosmetic / invisible; single output file, merged across iterations
8. **Master model update** (expert) — triggered by additional defects; auto-numbered; one active task at a time; immutable defect numbers; hash-checked against race with in-flight projections

## Example sequence

1. CT → QA → segmentation → master model created → projection = copy
2. UT → QA → segmentation → expert projection → auto comparison
3. Discrepancy: 1 UT defect not on projection (false positive) → master model update
4. Expert adds defect → all projections invalidated
5. Projections re-done for all modalities
6. Second review created for inspectors whose reads diverged
7. CT-HD: same cycle, no discrepancies
8. MCT: same cycle, no discrepancies

See `workflow_diagram.md` for the full flowchart.
