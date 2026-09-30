// Admin record types list: the sort is URL-addressable, and the rendered row
// order follows the domain rules — level in DICOM hierarchy, text columns
// case-insensitive with empty values last in both directions, ties broken by
// name. Views are rendered to HTML; effects never run.
import api/models
import cache
import clarinet_frontend/i18n
import gleam/dict
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import gleam/uri
import gleeunit/should
import lustre/element
import pages/record_types/list as record_types_list
import router
import shared

pub fn parse_route_keeps_sort_query_test() {
  let assert Ok(u) = uri.parse("/admin/record-types?sort=level&sort_dir=desc")
  router.parse_route(u)
  |> should.equal(
    router.AdminRecordTypes(
      dict.from_list([#("sort", "level"), #("sort_dir", "desc")]),
    ),
  )
}

pub fn level_sorts_by_hierarchy_with_name_tiebreak_test() {
  sorted_names("level", "asc")
  |> should.equal(["a_patient", "d_patient", "c_study", "b_series"])
  sorted_names("level", "desc")
  |> should.equal(["b_series", "c_study", "a_patient", "d_patient"])
}

pub fn label_sorts_case_insensitive_with_empty_last_test() {
  sorted_names("label", "asc")
  |> should.equal(["c_study", "d_patient", "b_series", "a_patient"])
  sorted_names("label", "desc")
  |> should.equal(["b_series", "d_patient", "c_study", "a_patient"])
}

// --- Helpers ---

fn sorted_names(col: String, dir: String) -> List(String) {
  let stats = [
    make_stats("b_series", Some("Zeta"), "SERIES"),
    make_stats("a_patient", None, "PATIENT"),
    make_stats("c_study", Some("alpha"), "STUDY"),
    make_stats("d_patient", Some("Beta"), "PATIENT"),
  ]
  let model =
    record_types_list.Model(
      active_filters: dict.from_list([#("sort", col), #("sort_dir", dir)]),
    )
  record_types_list.view(model, make_shared(stats))
  |> element.to_string
  |> row_names
}

/// Name cell of each body row, in document order.
fn row_names(html: String) -> List(String) {
  let assert Ok(#(_, body)) = string.split_once(html, "<tbody>")
  string.split(body, "<tr>")
  |> list.drop(1)
  |> list.map(fn(row) {
    let assert Ok(#(_, anchor)) = string.split_once(row, "<a ")
    let assert Ok(#(_, rest)) = string.split_once(anchor, ">")
    let assert Ok(#(name, _)) = string.split_once(rest, "<")
    name
  })
}

fn make_stats(
  name: String,
  label: Option(String),
  level: String,
) -> models.RecordTypeStats {
  models.RecordTypeStats(
    name: name,
    description: None,
    label: label,
    level: level,
    role_name: None,
    min_records: None,
    max_records: None,
    total_records: 0,
    records_by_status: models.RecordTypeStatusCounts(
      preparing: 0,
      blocked: 0,
      pending: 0,
      inwork: 0,
      finished: 0,
      failed: 0,
      pause: 0,
    ),
    unique_users: 0,
  )
}

fn make_shared(stats: List(models.RecordTypeStats)) -> shared.Shared {
  shared.Shared(
    user: None,
    route: router.AdminRecordTypes(dict.new()),
    previous_route: None,
    project_name: "",
    project_description: "",
    cache: cache.Model(..cache.init(), record_type_stats: Some(stats)),
    viewers: [],
    anon_per_study: False,
    dicomweb_backend: "builtin",
    registration_enabled: False,
    translate: i18n.translate(i18n.En, _),
    locale: i18n.En,
  )
}
