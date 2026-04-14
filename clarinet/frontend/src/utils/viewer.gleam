// Viewer URL helpers for OHIF, RadiAnt, and other external DICOM viewers

import api/info.{type ViewerInfo}
import api/types
import config
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import gleam/uri
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event

// --- URI builders ---

/// Build an OHIF viewer URL for a study, optionally scoped to a series.
pub fn ohif_url(study_uid: String, series_uid: Option(String)) -> String {
  let base =
    config.base_path() <> "/ohif/viewer?StudyInstanceUIDs=" <> study_uid
  case series_uid {
    Some(uid) -> base <> "&SeriesInstanceUIDs=" <> uid
    None -> base
  }
}

/// Build a RadiAnt URI for a study (queries PACS by StudyInstanceUID).
fn radiant_url(study_uid: String, pacs_name: String) -> String {
  let query =
    uri.query_to_string([
      #("n", "paet"),
      #("v", pacs_name),
      #("n", "pstv"),
      #("v", "0020000D"),
      #("v", study_uid),
    ])
  "radiant://?" <> query
}

/// Build a viewer URI for a given viewer config.
fn build_uri(
  viewer: ViewerInfo,
  study_uid: String,
  series_uid: Option(String),
) -> Option(String) {
  case viewer.name {
    "ohif" -> Some(ohif_url(study_uid, series_uid))
    "radiant" ->
      Some(radiant_url(study_uid, option.unwrap(viewer.pacs_name, "ORTHANC")))
    _ -> None
  }
}

fn viewer_label(name: String) -> String {
  case name {
    "ohif" -> "OHIF"
    "radiant" -> "RadiAnt"
    "weasis" -> "Weasis"
    _ -> name
  }
}

// --- Simple viewer buttons (links) for study/series pages ---

/// Render viewer link buttons for all configured viewers.
/// Returns element.none() if no study_uid or no viewers.
pub fn viewer_buttons(
  viewers: List(ViewerInfo),
  study_uid: Option(String),
  series_uid: Option(String),
  class: String,
) -> Element(msg) {
  case study_uid {
    None -> element.none()
    Some(uid) ->
      element.fragment(
        list.filter_map(viewers, fn(v) {
          case build_uri(v, uid, series_uid) {
            Some(url) ->
              Ok(html.a(
                [
                  attribute.href(url),
                  attribute.target("_blank"),
                  attribute.class(class),
                ],
                [html.text(viewer_label(v.name))],
              ))
            None -> Error(Nil)
          }
        }),
      )
  }
}

// --- Record viewer buttons (with preload support for OHIF) ---

/// Render viewer buttons for a record based on its DicomQueryLevel.
/// OHIF triggers on_view(url, study_uid) for preloading.
/// Other viewers open directly via link.
pub fn record_viewer_buttons(
  viewers: List(ViewerInfo),
  study_uid: Option(String),
  series_uid: Option(String),
  viewer_study_uids: Option(List(String)),
  viewer_series_uids: Option(List(String)),
  level: Option(types.DicomQueryLevel),
  viewer_mode: String,
  class: String,
  on_view: fn(String, String) -> msg,
) -> Element(msg) {
  case level {
    Some(types.Patient) | None -> element.none()
    Some(types.Study) | Some(types.Series) ->
      element.fragment(
        list.filter_map(viewers, fn(v) {
          case v.name {
            "ohif" ->
              Ok(ohif_record_button(
                study_uid,
                series_uid,
                viewer_study_uids,
                viewer_series_uids,
                level,
                viewer_mode,
                class,
                on_view,
              ))
            _ -> {
              let effective_uid = resolve_study_uid(study_uid, viewer_study_uids)
              case effective_uid {
                None -> Error(Nil)
                Some(uid) ->
                  case build_uri(v, uid, resolve_series_uid(series_uid, viewer_series_uids, level, viewer_mode)) {
                    Some(url) ->
                      Ok(html.a(
                        [
                          attribute.href(url),
                          attribute.target("_blank"),
                          attribute.class(class),
                        ],
                        [html.text(viewer_label(v.name))],
                      ))
                    None -> Error(Nil)
                  }
              }
            }
          }
        }),
      )
  }
}

fn resolve_study_uid(
  study_uid: Option(String),
  viewer_study_uids: Option(List(String)),
) -> Option(String) {
  case viewer_study_uids {
    Some(uids) if uids != [] -> list.first(uids) |> option.from_result
    _ -> study_uid
  }
}

fn resolve_series_uid(
  series_uid: Option(String),
  viewer_series_uids: Option(List(String)),
  level: Option(types.DicomQueryLevel),
  viewer_mode: String,
) -> Option(String) {
  case viewer_mode {
    "all_series" -> None
    _ ->
      case viewer_series_uids {
        Some(sids) if sids != [] -> list.first(sids) |> option.from_result
        _ ->
          case level {
            Some(types.Series) -> series_uid
            _ -> None
          }
      }
  }
}

/// OHIF-specific record button with preload support (preserves existing complex logic).
fn ohif_record_button(
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
  // Use viewer_study_uids if available and non-empty
  let uids = case viewer_study_uids {
    Some(uids) if uids != [] -> Some(uids)
    _ -> None
  }
  case uids {
    Some(uid_list) -> {
      let study_part = string.join(uid_list, "&StudyInstanceUIDs=")
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
        [html.text("OHIF")],
      )
    }
    None -> {
      let #(url, primary_uid) = case level, study_uid {
        Some(types.Series), Some(suid) -> #(
          ohif_url(suid, effective_series_uid),
          suid,
        )
        _, Some(suid) -> #(ohif_url(suid, None), suid)
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
            [html.text("OHIF")],
          )
      }
    }
  }
}
