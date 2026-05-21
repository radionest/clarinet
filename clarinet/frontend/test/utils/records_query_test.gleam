// Unit tests for utils/records_query.
import cache/bucket.{
  IdAsc, ModalityDesc, PatientAsc, RecordTypeDesc, StatusAsc, UserDesc,
}
import gleam/dict
import gleam/option.{None, Some}
import gleeunit/should
import utils/record_filters
import utils/records_query

// --- from_filters: maps user filter dict to RecordsQuery ---

pub fn from_filters_empty_test() {
  let q = records_query.from_filters(dict.new())
  q.patient_id |> should.equal(None)
  q.record_type_name |> should.equal(None)
  q.record_status |> should.equal(None)
  q.user_id |> should.equal(None)
  q.wo_user |> should.equal(False)
  // Defaults to IdAsc to match `default_sort_col = "id"` in the list pages,
  // so the column the UI arrows-up matches the order the backend returns.
  q.sort |> should.equal(IdAsc)
}

pub fn from_filters_status_test() {
  let q =
    records_query.from_filters(dict.from_list([#("status", "failed")]))
  q.record_status |> should.equal(Some("failed"))
}

pub fn from_filters_record_type_test() {
  let q =
    records_query.from_filters(dict.from_list([#("record_type", "biopsy")]))
  q.record_type_name |> should.equal(Some("biopsy"))
}

pub fn from_filters_patient_test() {
  let q = records_query.from_filters(dict.from_list([#("patient", "PAT42")]))
  q.patient_id |> should.equal(Some("PAT42"))
}

pub fn from_filters_user_uuid_test() {
  let q =
    records_query.from_filters(dict.from_list([#("user", "uid-xyz")]))
  q.user_id |> should.equal(Some("uid-xyz"))
  q.wo_user |> should.equal(False)
}

pub fn from_filters_user_unassigned_test() {
  // "__unassigned__" must translate to wo_user=true, user_id=None.
  let q =
    records_query.from_filters(
      dict.from_list([#("user", record_filters.unassigned_user_value)]),
    )
  q.user_id |> should.equal(None)
  q.wo_user |> should.equal(True)
}

pub fn from_filters_combined_test() {
  let filters =
    dict.from_list([
      #("status", "pending"),
      #("record_type", "biopsy"),
      #("patient", "PAT1"),
    ])
  let q = records_query.from_filters(filters)
  q.record_status |> should.equal(Some("pending"))
  q.record_type_name |> should.equal(Some("biopsy"))
  q.patient_id |> should.equal(Some("PAT1"))
}

// --- parse_sort_from_filters: filter dict → SortOrder ---

pub fn parse_sort_missing_defaults_to_id_asc_test() {
  records_query.parse_sort_from_filters(dict.new())
  |> should.equal(IdAsc)
}

pub fn parse_sort_patient_asc_test() {
  let filters =
    dict.from_list([#("sort", "patient"), #("sort_dir", "asc")])
  records_query.parse_sort_from_filters(filters)
  |> should.equal(PatientAsc)
}

pub fn parse_sort_status_asc_default_dir_test() {
  // No sort_dir defaults to ascending.
  let filters = dict.from_list([#("sort", "status")])
  records_query.parse_sort_from_filters(filters)
  |> should.equal(StatusAsc)
}

pub fn parse_sort_record_type_desc_test() {
  let filters =
    dict.from_list([#("sort", "record_type"), #("sort_dir", "desc")])
  records_query.parse_sort_from_filters(filters)
  |> should.equal(RecordTypeDesc)
}

pub fn parse_sort_user_desc_test() {
  let filters = dict.from_list([#("sort", "user"), #("sort_dir", "desc")])
  records_query.parse_sort_from_filters(filters) |> should.equal(UserDesc)
}

pub fn parse_sort_modality_desc_test() {
  let filters =
    dict.from_list([#("sort", "modality"), #("sort_dir", "desc")])
  records_query.parse_sort_from_filters(filters)
  |> should.equal(ModalityDesc)
}

pub fn parse_sort_unknown_column_falls_back_test() {
  let filters = dict.from_list([#("sort", "made_up_column")])
  records_query.parse_sort_from_filters(filters)
  |> should.equal(IdAsc)
}

// --- with_user_scope: layers user filter on a query ---

pub fn with_user_scope_overrides_user_id_test() {
  let scoped =
    records_query.with_user_scope(bucket.default_query(), "user-42")
  scoped.user_id |> should.equal(Some("user-42"))
  scoped.wo_user |> should.equal(False)
}

pub fn with_user_scope_clears_wo_user_test() {
  let q = bucket.RecordsQuery(..bucket.default_query(), wo_user: True)
  let scoped = records_query.with_user_scope(q, "user-42")
  scoped.wo_user |> should.equal(False)
  scoped.user_id |> should.equal(Some("user-42"))
}

// --- from_filters_for_X: scoped helpers for detail pages ---

pub fn from_filters_for_patient_test() {
  let q = records_query.from_filters_for_patient(dict.new(), "PAT99")
  q.patient_id |> should.equal(Some("PAT99"))
}

pub fn from_filters_for_study_test() {
  let q = records_query.from_filters_for_study(dict.new(), "1.2.3")
  q.study_uid |> should.equal(Some("1.2.3"))
}

pub fn from_filters_for_record_type_test() {
  let q = records_query.from_filters_for_record_type(dict.new(), "biopsy")
  q.record_type_name |> should.equal(Some("biopsy"))
}
