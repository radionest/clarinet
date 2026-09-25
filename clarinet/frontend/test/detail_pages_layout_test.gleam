// Layout of the entity detail pages (record, patient, study, series): the
// secondary sections (activity feed, workflow graph) sit after the page's
// actions as collapsed <details>, titles name the entity instead of its raw
// UID, and each related entity is linked once — no duplicate parent cards or
// per-row "View" buttons. Views are rendered to HTML; effects never run.
import api/info
import api/models
import api/types
import cache
import cache/bucket
import clarinet_frontend/i18n
import gleam/dict
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import gleeunit/should
import lustre/element
import pages/patients/detail as patient_detail
import pages/records/execute
import pages/series/detail as series_detail
import pages/studies/detail as study_detail
import router
import shared
import utils/load_status
import utils/records_query

// --- Record page ---

pub fn record_page_collapses_activity_and_workflow_after_actions_test() {
  let html = render_record(make_record(42))
  let assert Ok(#(content, tail)) = string.split_once(html, "page-actions")
  content |> string.contains("<details") |> should.be_false
  let tags = details_tags(tail)
  tags |> list.length |> should.equal(2)
  tags |> list.any(string.contains(_, "open")) |> should.be_false
  let assert Ok(#(_, after_activity)) =
    string.split_once(tail, "<summary>Activity</summary>")
  after_activity
  |> string.contains("<summary>Workflow</summary>")
  |> should.be_true
}

pub fn record_page_title_is_record_type_label_test() {
  models.Record(..make_record(42), record_type: Some(make_record_type()))
  |> render_record
  |> h1_text
  |> should.equal("Liver segmentation")
}

pub fn record_page_title_falls_back_to_type_name_test() {
  make_record(42) |> render_record |> h1_text |> should.equal("liver_seg")
}

// --- Patient page ---

pub fn patient_page_ends_with_delete_then_collapsed_activity_test() {
  let html = render_patient()
  let assert Ok(#(_, after_records)) =
    string.split_once(html, "<h3>Records</h3>")
  let assert Ok(#(before_delete, after_delete)) =
    string.split_once(after_records, "Delete Patient")
  before_delete |> string.contains("<details") |> should.be_false
  let tags = details_tags(after_delete)
  tags |> list.length |> should.equal(1)
  tags |> list.any(string.contains(_, "open")) |> should.be_false
  after_delete
  |> string.contains("<summary>Activity</summary>")
  |> should.be_true
}

pub fn patient_page_searches_pacs_from_studies_card_test() {
  let assert Ok(#(_, from_studies)) =
    string.split_once(render_patient(), "<h3>Studies</h3>")
  let assert Ok(#(before_search, _)) =
    string.split_once(from_studies, "Search PACS")
  before_search |> string.contains("class=\"card\"") |> should.be_false
}

pub fn patient_page_links_each_study_and_record_once_test() {
  let html = render_patient()
  links_to(html, "/studies/1.2.3") |> should.equal(1)
  links_to(html, "/records/7") |> should.equal(1)
}

// --- Study page ---

pub fn study_page_title_reads_description_and_date_test() {
  render_study(Some("CT Chest"))
  |> h1_text
  |> should.equal("CT Chest (2024-03-01)")
}

pub fn study_page_title_falls_back_to_study_and_date_test() {
  render_study(None) |> h1_text |> should.equal("Study (2024-03-01)")
}

pub fn study_page_links_patient_and_each_record_once_test() {
  let html = render_study(Some("CT Chest"))
  links_to(html, "/patients/P001") |> should.equal(1)
  links_to(html, "/records/7") |> should.equal(1)
}

pub fn study_page_puts_delete_after_records_test() {
  let assert Ok(#(_, after_records)) =
    string.split_once(render_study(Some("CT Chest")), "<h3>Records</h3>")
  after_records |> string.contains("Delete Study") |> should.be_true
}

pub fn study_page_shows_viewer_column_only_with_viewers_test() {
  render_study_series([])
  |> string.contains("<th>Actions</th>")
  |> should.be_false
  render_study_series([info.ViewerInfo(name: "ohif", pacs_name: None)])
  |> string.contains("<th>Actions</th>")
  |> should.be_true
}

// --- Series page ---

pub fn series_page_title_reads_number_and_description_test() {
  render_series(Some("T2 AX")) |> h1_text |> should.equal("Series 3 — T2 AX")
}

pub fn series_page_title_falls_back_to_number_test() {
  render_series(None) |> h1_text |> should.equal("Series 3")
}

pub fn series_page_links_study_and_patient_once_test() {
  let html = render_series(Some("T2 AX"))
  links_to(html, "/studies/1.2.3") |> should.equal(1)
  links_to(html, "/patients/P001") |> should.equal(1)
}

// --- Rendering ---

fn render_record(record: models.Record) -> String {
  let ctx = make_shared(cache.put_record(cache.init(), record))
  let #(model, _eff, _out) = execute.init("42", ctx)
  execute.view(
    execute.Model(..model, record_load_status: load_status.Loaded),
    ctx,
  )
  |> element.to_string
}

fn render_patient() -> String {
  let key =
    bucket.Records(records_query.with_patient_scope(
      records_query.from_filters(dict.new()),
      "P001",
    ))
  let ctx =
    cache.init()
    |> cache.put_patient(make_patient())
    |> with_bucket(key, [make_record(7), make_record(8)])
    |> make_shared
  let #(model, _eff, _out) = patient_detail.init("P001", ctx)
  patient_detail.view(
    patient_detail.Model(..model, patient_load_status: load_status.Loaded),
    ctx,
  )
  |> element.to_string
}

fn render_study(description: Option(String)) -> String {
  let key = bucket.Records(bucket.query_with_study("1.2.3"))
  let ctx =
    cache.init()
    |> cache.put_study(make_study(description))
    |> with_bucket(key, [make_record(7), make_record(8)])
    |> make_shared
  study_detail.view(
    study_detail.Model(study_uid: "1.2.3", load_status: load_status.Loaded),
    ctx,
  )
  |> element.to_string
}

/// Study page with one series row, under the given viewer configuration.
fn render_study_series(viewers: List(info.ViewerInfo)) -> String {
  let series = models.Series(..make_series(None), study: None, records: None)
  let study = models.Study(..make_study(None), series: Some([series]))
  let ctx =
    shared.Shared(
      ..make_shared(cache.put_study(cache.init(), study)),
      viewers: viewers,
    )
  study_detail.view(
    study_detail.Model(study_uid: "1.2.3", load_status: load_status.Loaded),
    ctx,
  )
  |> element.to_string
}

fn render_series(description: Option(String)) -> String {
  let ctx =
    make_shared(cache.put_series(cache.init(), make_series(description)))
  series_detail.view(
    series_detail.Model(series_uid: "1.2.3.4", load_status: load_status.Loaded),
    ctx,
  )
  |> element.to_string
}

// --- HTML probes ---

/// Opening `<details …>` tags, in document order.
fn details_tags(html: String) -> List(String) {
  string.split(html, "<details")
  |> list.drop(1)
  |> list.map(fn(chunk) {
    let assert Ok(#(attrs, _)) = string.split_once(chunk, ">")
    "<details" <> attrs <> ">"
  })
}

fn h1_text(html: String) -> String {
  let assert Ok(#(_, rest)) = string.split_once(html, "<h1>")
  let assert Ok(#(text, _)) = string.split_once(rest, "</h1>")
  text
}

/// Anchors pointing at `path`; suffix match keeps it base-path agnostic.
fn links_to(html: String, path: String) -> Int {
  list.length(string.split(html, path <> "\"")) - 1
}

// --- Fixtures ---

fn make_shared(c: cache.Model) -> shared.Shared {
  shared.Shared(
    user: Some(
      models.User(
        id: "admin",
        email: "admin@example.com",
        is_active: True,
        is_superuser: True,
        is_verified: True,
        role_names: [],
        capabilities: [],
      ),
    ),
    route: router.Home,
    previous_route: None,
    project_name: "",
    project_description: "",
    cache: c,
    viewers: [],
    anon_per_study: False,
    dicomweb_backend: "builtin",
    registration_enabled: False,
    translate: i18n.translate(i18n.En, _),
    locale: i18n.En,
  )
}

fn with_bucket(
  c: cache.Model,
  key: bucket.BucketKey,
  items: List(models.Record),
) -> cache.Model {
  let b =
    bucket.Bucket(
      key: key,
      status: bucket.Live(0),
      items: items,
      next_cursor: None,
    )
  cache.Model(
    ..c,
    record_buckets: dict.insert(c.record_buckets, bucket.key_to_topic(key), b),
  )
}

fn base_patient() -> models.Patient {
  models.Patient(
    id: "P001",
    name: Some("Jane Roe"),
    anon_id: None,
    anon_name: None,
    auto_id: None,
    studies: None,
    records: None,
  )
}

fn make_patient() -> models.Patient {
  models.Patient(
    ..base_patient(),
    studies: Some([make_study(Some("CT Chest"))]),
  )
}

fn make_study(description: Option(String)) -> models.Study {
  models.Study(
    study_uid: "1.2.3",
    date: "2024-03-01",
    anon_uid: None,
    study_description: description,
    modalities_in_study: None,
    patient_id: "P001",
    patient: Some(base_patient()),
    series: None,
    records: None,
  )
}

fn make_series(description: Option(String)) -> models.Series {
  models.Series(
    series_uid: "1.2.3.4",
    series_description: description,
    series_number: 3,
    modality: Some("MR"),
    instance_count: None,
    anon_uid: None,
    study_uid: "1.2.3",
    study: Some(make_study(Some("CT Chest"))),
    records: Some([make_record(7), make_record(8)]),
  )
}

fn make_record(id: Int) -> models.Record {
  models.Record(
    id: Some(id),
    context_info: None,
    context_info_html: None,
    status: types.Pending,
    study_uid: Some("1.2.3"),
    series_uid: Some("1.2.3.4"),
    record_type_name: "liver_seg",
    user_id: None,
    patient_id: "P001",
    parent_record_id: None,
    study_anon_uid: None,
    series_anon_uid: None,
    viewer_study_uids: None,
    viewer_series_uids: None,
    clarinet_storage_path: None,
    files: None,
    file_checksums: None,
    file_links: None,
    patient: None,
    study: None,
    series: None,
    record_type: None,
    data: None,
    created_at: None,
    changed_at: None,
    started_at: None,
    finished_at: None,
    radiant: None,
    display_anon_id: None,
    is_editable: True,
    shared_editing: False,
  )
}

fn make_record_type() -> models.RecordType {
  models.RecordType(
    name: "liver_seg",
    description: None,
    label: Some("Liver segmentation"),
    slicer_script: None,
    slicer_script_args: None,
    slicer_result_validator: None,
    slicer_result_validator_args: None,
    data_schema: None,
    ui_schema: None,
    role_name: None,
    max_records: None,
    min_records: None,
    unique_by: None,
    parent_required: False,
    inherit_user_from_parent: False,
    editable: True,
    edit_window_days: None,
    viewer_mode: "single_series",
    allowed_viewers: None,
    level: types.Series,
    file_registry: None,
    constraint_role: None,
    records: None,
  )
}
