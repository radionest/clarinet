// Universal search: route parsing/serialization and page rendering.
// Views are rendered to HTML; effects never run.
import api/models
import api/search.{type SearchResults, SearchResults}
import cache
import clarinet_frontend/i18n
import gleam/option.{None, Some}
import gleam/string
import gleam/uri
import gleeunit/should
import lustre/element
import pages/search as search_page
import router
import shared

pub fn parse_search_route_test() {
  let assert Ok(u) = uri.parse("/search?q=CLARINET_12")
  router.parse_route(u) |> should.equal(router.Search("CLARINET_12"))
}

pub fn parse_search_route_without_query_test() {
  let assert Ok(u) = uri.parse("/search")
  router.parse_route(u) |> should.equal(router.Search(""))
}

pub fn search_route_requires_admin_test() {
  router.requires_admin_role(router.Search("x")) |> should.be_true
}

pub fn search_route_to_query_encodes_test() {
  router.route_to_query(router.Search("a b"))
  |> should.equal(Some("q=a%20b"))
}

pub fn search_route_path_test() {
  router.route_to_path(router.Search("x")) |> should.equal("/search")
}

// --- Page rendering with a fixture of results ---

fn make_shared() -> shared.Shared {
  shared.Shared(
    user: None,
    route: router.Search("fixture"),
    previous_route: None,
    project_name: "",
    project_description: "",
    cache: cache.init(),
    viewers: [],
    anon_per_study: False,
    dicomweb_backend: "builtin",
    registration_enabled: False,
    translate: i18n.translate(i18n.En, _),
    locale: i18n.En,
  )
}

fn make_patient(id: String, name: String) -> models.Patient {
  models.Patient(
    id: id,
    name: Some(name),
    anon_id: Some("CLARINET_1"),
    anon_name: None,
    auto_id: Some(1),
    studies: None,
    records: None,
  )
}

fn make_study(uid: String, patient_id: String) -> models.Study {
  models.Study(
    study_uid: uid,
    date: "2026-01-02",
    anon_uid: Some("2.25.1"),
    study_description: Some("CT Chest"),
    modalities_in_study: None,
    patient_id: patient_id,
    patient: Some(make_patient(patient_id, "Fixture")),
    series: None,
    records: None,
  )
}

fn fixture_results() -> SearchResults {
  SearchResults(
    patients: [make_patient("PAT_9", "Fixture")],
    studies: [make_study("1.2.840.9", "PAT_9")],
  )
}

pub fn search_page_renders_both_sections_test() {
  let model =
    search_page.Model(query: "fixture", results: Some(fixture_results()), loading: False)
  let html = search_page.view(model, make_shared()) |> element.to_string
  html |> string.contains("Patients (1)") |> should.be_true
  html |> string.contains("Studies (1)") |> should.be_true
  html |> string.contains("href=\"/patients/PAT_9\"") |> should.be_true
  html |> string.contains("href=\"/studies/1.2.840.9\"") |> should.be_true
  html |> string.contains("CT Chest (2026-01-02)") |> should.be_true
}

pub fn search_page_empty_results_test() {
  let model =
    search_page.Model(
      query: "fixture",
      results: Some(SearchResults(patients: [], studies: [])),
      loading: False,
    )
  let html = search_page.view(model, make_shared()) |> element.to_string
  html |> string.contains("Nothing found.") |> should.be_true
}
