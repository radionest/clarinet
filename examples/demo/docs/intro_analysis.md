# Methodology notes: absence of an absolute ground truth in NDT defect detection

This study's master-model design addresses a well-documented problem in
non-destructive testing (NDT) reliability research: there is rarely an
absolute, independently verifiable ground truth for whether a given
indication is a true defect.

## The "true-state" problem in POD studies

Probability-of-Detection (POD) methodology (see MIL-HDBK-1823A, the
standard reference for NDT reliability assessment) faces the same paradox
as expert-scored image analysis in other fields: using an expert's call as
the reference standard to validate a more objective or more sensitive
method is circular when the expert's call is itself uncertain. This is why
POD studies typically require an independent "truth" determination —
destructive sectioning, a higher-fidelity reference modality, or a
consensus panel — rather than a single inspector's read.

## Incorporation bias across modalities

When a study evaluates multiple NDT modalities on the same part, showing
an inspector the result of one modality before they read another
introduces incorporation bias: knowledge of a prior finding can inflate
apparent sensitivity of the later read (a missed indication on the second
modality gets excused as "already known") or deflate its apparent
specificity (an indication only visible on the more sensitive modality
gets miscounted as a false call). This is the direct justification for
this study's strict modality isolation and controlled viewing sequence —
inspectors read the least sensitive method first and are not shown prior
results until their independent read is submitted.

## Masking / false-call sources

A method that resolves finer detail than the reference standard will
surface indications the reference can't confirm or deny. Rather than
discarding those as noise, this design routes them through the
**second-review** stage, which separates two distinct causes: a *method
limitation* (the defect genuinely isn't resolvable on that modality —
classified "invisible") from an *observer miss* (the defect is visible but
was skipped — classified defect/indeterminate/cosmetic). Distinguishing
these is standard practice in POD analysis, where "hit/miss" data is only
meaningful once observer error is separated from detectability limits.

## Incomplete verification

Even destructive sectioning only samples the fragment actually removed
during repair — indications outside that fragment are never verified
against ground truth. This mirrors the incomplete-verification problem in
any study where the reference standard (destructive) only covers
part of the object under test. The master model's iterative,
multi-modality construction is this study's answer to that same
constraint: rather than requiring a single perfect reference, it builds
one incrementally from the most reliable available signal at each stage,
consistent with how POD reliability programs bootstrap a working "truth"
data set when no absolute one exists.
