// OHIF Viewer URL helpers for study/series viewer buttons

import api/types
import config
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event

/// Build an OHIF viewer URL for a study, optionally scoped to a series.
pub fn viewer_url(study_uid: String, series_uid: Option(String)) -> String {
  let base =
    config.base_path() <> "/ohif/viewer?StudyInstanceUIDs=" <> study_uid
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
/// Clicking triggers on_view(viewer_url, first_study_uid) for preloading.
/// PATIENT or unknown level -> no button; STUDY -> study URL; SERIES -> study+series URL.
pub fn record_viewer_button(
  study_uid: Option(String),
  series_uid: Option(String),
  viewer_study_uids: Option(List(String)),
  viewer_series_uids: Option(List(String)),
  level: Option(types.DicomQueryLevel),
  viewer_mode: String,
  class: String,
  on_view: fn(String, String) -> msg,
) -> Element(msg) {
  // In "all_series" mode, don't pass series_uid to OHIF
  let effective_series_uid = case viewer_mode {
    "all_series" -> None
    _ -> series_uid
  }
  case level {
    Some(types.Study) | Some(types.Series) -> {
      // Use viewer_study_uids if available and non-empty
      let uids = case viewer_study_uids {
        Some(uids) if uids != [] -> Some(uids)
        _ -> None
      }
      case uids {
        Some(uid_list) -> {
          let study_part =
            string.join(uid_list, "&StudyInstanceUIDs=")
          let url =
            config.base_path()
            <> "/ohif/viewer?StudyInstanceUIDs="
            <> study_part
          // Determine series UIDs to include (skip in all_series mode)
          let series_part = case viewer_mode {
            "all_series" -> ""
            _ ->
              case viewer_series_uids {
                Some(sids) if sids != [] ->
                  "&SeriesInstanceUIDs="
                  <> string.join(sids, "&SeriesInstanceUIDs=")
                _ ->
                  case level, effective_series_uid {
                    Some(types.Series), Some(s) -> "&SeriesInstanceUIDs=" <> s
                    _, _ -> ""
                  }
              }
          }
          let url = url <> series_part
          // First study UID for preload
          let first_uid = list.first(uid_list) |> option.from_result
          let primary_uid = case first_uid {
            Some(u) -> u
            None -> ""
          }
          html.button(
            [
              attribute.class(class),
              event.on_click(on_view(url, primary_uid)),
            ],
            [html.text("Open in Viewer")],
          )
        }
        None -> {
          let #(url, primary_uid) = case level, study_uid {
            Some(types.Series), Some(suid) -> #(
              viewer_url(suid, effective_series_uid),
              suid,
            )
            _, Some(suid) -> #(viewer_url(suid, None), suid)
            _, None -> #("", "")
          }
          case primary_uid {
            "" -> element.none()
            _ ->
              html.button(
                [
                  attribute.class(class),
                  event.on_click(on_view(url, primary_uid)),
                ],
                [html.text("Open in Viewer")],
              )
          }
        }
      }
    }
    Some(types.Patient) | None -> element.none()
  }
}
