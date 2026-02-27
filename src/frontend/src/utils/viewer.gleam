// OHIF Viewer URL helpers for study/series viewer buttons

import api/types
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html

/// Build an OHIF viewer URL for a study, optionally scoped to a series.
pub fn viewer_url(study_uid: String, series_uid: Option(String)) -> String {
  let base = "/ohif/viewer?StudyInstanceUIDs=" <> study_uid
  case series_uid {
    Some(uid) -> base <> "&SeriesInstanceUIDs=" <> uid
    None -> base
  }
}

/// Render an "Open in Viewer" link button. Returns element.none() if no study_uid.
pub fn viewer_button(
  study_uid: Option(String),
  series_uid: Option(String),
  class: String,
) -> Element(msg) {
  case study_uid {
    Some(uid) ->
      html.a(
        [
          attribute.href(viewer_url(uid, series_uid)),
          attribute.target("_blank"),
          attribute.class(class),
        ],
        [html.text("Open in Viewer")],
      )
    None -> element.none()
  }
}

/// Render a viewer button for a record based on its DicomQueryLevel.
/// PATIENT or unknown level -> no button; STUDY -> study URL; SERIES -> study+series URL.
pub fn record_viewer_button(
  study_uid: Option(String),
  series_uid: Option(String),
  level: Option(types.DicomQueryLevel),
  class: String,
) -> Element(msg) {
  case level {
    Some(types.Study) -> viewer_button(study_uid, None, class)
    Some(types.Series) -> viewer_button(study_uid, series_uid, class)
    Some(types.Patient) | None -> element.none()
  }
}
