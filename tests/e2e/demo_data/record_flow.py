"""RecordFlow workflow definitions for the Clarinet demo project."""

from clarinet.services.recordflow import record, series

# Flow 1: When doctor-review finishes -> always create ai-analysis
(record("doctor-review").on_status("finished").add_record("ai-analysis"))

# Flow 2: When doctor-review finishes with low confidence -> create expert-check
(
    record("doctor-review")
    .on_status("finished")
    .if_(record("doctor-review").data.confidence < 70)
    .add_record("expert-check")
)

# Flow 3: When ai-analysis finishes and disagrees with doctor -> create expert-check
(
    record("ai-analysis")
    .on_status("finished")
    .if_(record("ai-analysis").data.ai_diagnosis != record("doctor-review").data.diagnosis)
    .add_record("expert-check")
)

# Flow 4: When a new series is created -> auto-create series-markup record
(series().on_created().add_record("series-markup"))

(record("series-markup").on_status("finished").add_record("lesion-seg"))
