"""RecordFlow workflow definitions for the Clarinet demo project."""

from src.services.recordflow import record, series

# Flow 1: When doctor_review finishes -> always create ai_analysis
(record("doctor_review").on_status("finished").add_record("ai_analysis"))

# Flow 2: When doctor_review finishes with low confidence -> create expert_check
(
    record("doctor_review")
    .on_status("finished")
    .if_(record("doctor_review").data.confidence < 70)
    .add_record("expert_check")
)

# Flow 3: When ai_analysis finishes and disagrees with doctor -> create expert_check
(
    record("ai_analysis")
    .on_status("finished")
    .if_(record("ai_analysis").data.ai_diagnosis != record("doctor_review").data.diagnosis)
    .add_record("expert_check")
)

# Flow 4: When a new series is created -> auto-create series_markup record
(series().on_created().add_record("series_markup"))
