// Inline navigation links to a single entity's detail page. Centralises the
// `<a class="link">id</a>` markup that was previously copy-pasted across the
// patient/study/series/record tables so every id renders the same way.
//
// Patient/Study/Series detail routes are admin-only
// (`router.requires_admin_role`). On pages a non-admin can also reach (the
// shared records widget, the record detail page) callers use `patient_if_admin`,
// which falls back to plain text rather than emit a link that would dead-end on
// the redirect to Home. On admin-only pages (patient/study/series detail) the
// plain `patient`/`study`/`study_labeled`/`series` helpers are safe — the route
// guard guarantees the viewer is an admin. The record detail route is open to
// any authenticated user, so `record` is always a live link.
import api/models.{type Study}
import gleam/int
import gleam/option
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import router

fn anchor(href: String, label: String) -> Element(msg) {
  html.a([attribute.href(href), attribute.class("link")], [html.text(label)])
}

/// Link to a record's detail page. Open to any authenticated user.
pub fn record(id: Int) -> Element(msg) {
  let id_str = int.to_string(id)
  anchor(router.route_to_path(router.RecordDetail(id_str)), id_str)
}

/// Link to a patient's detail page (admin-only route).
pub fn patient(patient_id: String) -> Element(msg) {
  anchor(router.route_to_path(router.PatientDetail(patient_id)), patient_id)
}

/// Link to a study's detail page (admin-only route).
pub fn study(study_uid: String) -> Element(msg) {
  anchor(router.route_to_path(router.StudyDetail(study_uid)), study_uid)
}

/// Study link with custom text, e.g. a date or `study_title` (admin-only route).
pub fn study_labeled(study_uid: String, label: String) -> Element(msg) {
  anchor(router.route_to_path(router.StudyDetail(study_uid)), label)
}

/// Readable name for a study — "CT Chest (2024-03-01)", or "Study (date)"
/// without a description — used instead of the raw DICOM UID.
pub fn study_title(study: Study) -> String {
  option.unwrap(study.study_description, "Study") <> " (" <> study.date <> ")"
}

/// Link to a series' detail page (admin-only route).
pub fn series(series_uid: String) -> Element(msg) {
  anchor(router.route_to_path(router.SeriesDetail(series_uid)), series_uid)
}

/// Patient link for pages non-admins can also see: a live link for admins,
/// plain text otherwise (the patient detail page is admin-only).
pub fn patient_if_admin(patient_id: String, is_admin: Bool) -> Element(msg) {
  case is_admin {
    True -> patient(patient_id)
    False -> html.text(patient_id)
  }
}

/// Study link whose visible text may differ from the uid (e.g. a description):
/// a live link for admins, plain text otherwise (the study page is admin-only).
pub fn study_labeled_if_admin(
  study_uid: String,
  label: String,
  is_admin: Bool,
) -> Element(msg) {
  case is_admin {
    True -> study_labeled(study_uid, label)
    False -> html.text(label)
  }
}

/// Series link whose visible text may differ from the uid: a live link for
/// admins, plain text otherwise (the series page is admin-only).
pub fn series_labeled_if_admin(
  series_uid: String,
  label: String,
  is_admin: Bool,
) -> Element(msg) {
  case is_admin {
    True -> anchor(router.route_to_path(router.SeriesDetail(series_uid)), label)
    False -> html.text(label)
  }
}
