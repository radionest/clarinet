// Patient detail page (admin only)
import api/models.{
  type PacsSeriesResult, type PacsStudyWithSeries, type Patient, type Record,
  type Study,
}
import utils/status
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}

pub fn view(model: Model, patient_id: String) -> Element(Msg) {
  case dict.get(model.patients, patient_id) {
    Ok(patient) -> render_detail(model, patient)
    Error(_) -> loading_view(patient_id)
  }
}

fn render_detail(model: Model, patient: Patient) -> Element(Msg) {
  let patient_records =
    dict.values(model.records)
    |> list.filter(fn(r) { r.patient_id == patient.id })
    |> list.sort(fn(a, b) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    })

  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Patient: " <> patient.id)]),
      html.div([], [
        html.button(
          [
            attribute.class("btn btn-secondary"),
            event.on_click(store.Navigate(router.Patients)),
          ],
          [html.text("Back to Patients")],
        ),
        html.button(
          [
            attribute.class("btn btn-danger"),
            event.on_click(store.OpenModal(store.ConfirmDelete(
              "patient",
              patient.id,
            ))),
          ],
          [html.text("Delete Patient")],
        ),
      ]),
    ]),
    patient_info_card(patient),
    studies_section(patient.studies),
    pacs_section(model, patient),
    records_section(patient_records),
  ])
}

fn patient_info_card(patient: Patient) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Patient Information")]),
    html.dl([attribute.class("record-metadata")], [
      html.dt([], [html.text("ID:")]),
      html.dd([], [html.text(patient.id)]),
      html.dt([], [html.text("Name:")]),
      html.dd([], [html.text(option.unwrap(patient.name, "-"))]),
      html.dt([], [html.text("Anonymous ID:")]),
      html.dd([], [html.text(option.unwrap(patient.anon_id, "-"))]),
      html.dt([], [html.text("Anonymous Name:")]),
      html.dd([], [html.text(option.unwrap(patient.anon_name, "-"))]),
    ]),
    anonymize_button(patient),
  ])
}

fn anonymize_button(patient: Patient) -> Element(Msg) {
  case patient.anon_name {
    None ->
      html.div([attribute.class("card-actions")], [
        html.button(
          [
            attribute.class("btn btn-primary"),
            event.on_click(store.AnonymizePatient(patient.id)),
          ],
          [html.text("Anonymize Patient")],
        ),
      ])
    Some(_) -> element.none()
  }
}

fn studies_section(studies: Option(List(Study))) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Studies")]),
    case studies {
      None | Some([]) ->
        html.p([attribute.class("text-muted")], [
          html.text("No studies found for this patient."),
        ])
      Some(study_list) ->
        html.div([attribute.class("table-responsive")], [
          html.table([attribute.class("table")], [
            html.thead([], [
              html.tr([], [
                html.th([], [html.text("Date")]),
                html.th([], [html.text("Modalities")]),
                html.th([], [html.text("Description")]),
                html.th([], [html.text("Series")]),
                html.th([], [html.text("Anonymized")]),
                html.th([], [html.text("Actions")]),
              ]),
            ]),
            html.tbody([], list.map(study_list, study_row)),
          ]),
        ])
    },
  ])
}

fn study_row(study: Study) -> Element(Msg) {
  let #(modalities, description, series_count) = case study.series {
    Some(series_list) -> {
      let mods =
        series_list
        |> list.filter_map(fn(s) { option.to_result(s.modality, Nil) })
        |> list.unique
      let mods_str = case mods {
        [] -> "-"
        _ -> string.join(mods, "/")
      }
      let desc = case
        list.filter_map(series_list, fn(s) {
          option.to_result(s.series_description, Nil)
        })
      {
        [first, ..] -> first
        [] -> "-"
      }
      #(mods_str, desc, int.to_string(list.length(series_list)))
    }
    None -> #("-", "-", "0")
  }

  html.tr([], [
    html.td([], [html.text(study.date)]),
    html.td([], [html.text(modalities)]),
    html.td([], [html.text(description)]),
    html.td([], [html.text(series_count)]),
    html.td([], [
      case study.anon_uid {
        Some(_) ->
          html.span([attribute.class("badge badge-success")], [
            html.text("Yes"),
          ])
        None ->
          html.span([attribute.class("badge badge-muted")], [
            html.text("No"),
          ])
      },
    ]),
    html.td([], [
      html.a(
        [
          attribute.href(router.route_to_path(router.StudyDetail(study.study_uid))),
          attribute.class("btn btn-sm btn-outline"),
        ],
        [html.text("View")],
      ),
    ]),
  ])
}

fn records_section(records: List(Record)) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Records")]),
    case records {
      [] ->
        html.p([attribute.class("text-muted")], [
          html.text("No records found for this patient."),
        ])
      _ ->
        html.div([attribute.class("table-responsive")], [
          html.table([attribute.class("table")], [
            html.thead([], [
              html.tr([], [
                html.th([], [html.text("ID")]),
                html.th([], [html.text("Type")]),
                html.th([], [html.text("Status")]),
                html.th([], [html.text("Actions")]),
              ]),
            ]),
            html.tbody([], list.map(records, record_row)),
          ]),
        ])
    },
  ])
}

fn record_row(record: Record) -> Element(Msg) {
  let record_id = option.unwrap(record.id, 0)
  let record_id_str = int.to_string(record_id)

  let type_label = case record.record_type {
    Some(rt) -> option.unwrap(rt.label, rt.name)
    None -> record.record_type_name
  }

  html.tr([], [
    html.td([], [html.text(record_id_str)]),
    html.td([], [html.text(type_label)]),
    html.td([], [html.text(status.display_text(record.status))]),
    html.td([], [
      html.a(
        [
          attribute.href("/records/" <> record_id_str),
          attribute.class("btn btn-sm btn-outline"),
        ],
        [html.text("View")],
      ),
    ]),
  ])
}

fn pacs_section(model: Model, patient: Patient) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Add Study from PACS")]),
    html.div([attribute.class("card-actions")], [
      html.button(
        [
          attribute.class("btn btn-primary"),
          attribute.disabled(model.pacs_loading),
          event.on_click(store.SearchPacsStudies(patient.id)),
        ],
        [
          case model.pacs_loading {
            True -> html.text("Searching...")
            False -> html.text("Search PACS")
          },
        ],
      ),
      case model.pacs_studies {
        [] -> element.none()
        _ ->
          html.button(
            [
              attribute.class("btn btn-secondary"),
              event.on_click(store.ClearPacsResults),
            ],
            [html.text("Clear Results")],
          )
      },
    ]),
    case model.pacs_loading {
      True ->
        html.div([attribute.class("loading-container")], [
          html.div([attribute.class("spinner")], []),
          html.p([], [html.text("Searching PACS...")]),
        ])
      False ->
        case model.pacs_studies {
          [] -> element.none()
          pacs_studies -> pacs_results_table(model, pacs_studies, patient.id)
        }
    },
  ])
}

fn pacs_results_table(
  model: Model,
  pacs_studies: List(PacsStudyWithSeries),
  patient_id: String,
) -> Element(Msg) {
  html.div([attribute.class("table-responsive")], [
    html.table([attribute.class("table")], [
      html.thead([], [
        html.tr([], [
          html.th([], [html.text("Study Date")]),
          html.th([], [html.text("Modalities")]),
          html.th([], [html.text("Description")]),
          html.th([], [html.text("Series")]),
          html.th([], [html.text("Actions")]),
        ]),
      ]),
      html.tbody(
        [],
        list.flat_map(pacs_studies, fn(ps) {
          pacs_study_rows(model, ps, patient_id)
        }),
      ),
    ]),
  ])
}

fn pacs_study_rows(
  model: Model,
  ps: PacsStudyWithSeries,
  patient_id: String,
) -> List(Element(Msg)) {
  let study_date = format_dicom_date(ps.study.study_date)
  let modalities = option.unwrap(ps.study.modalities_in_study, "-")
  let description = option.unwrap(ps.study.study_description, "-")
  let series_count = list.length(ps.series)
  let is_importing = model.pacs_importing == Some(ps.study.study_instance_uid)

  let study_row =
    html.tr([], [
      html.td([], [html.text(study_date)]),
      html.td([], [html.text(modalities)]),
      html.td([], [html.text(description)]),
      html.td([], [html.text(int.to_string(series_count) <> " series")]),
      html.td([], [
        case ps.already_exists {
          True ->
            html.span([attribute.class("badge badge-muted")], [
              html.text("Already added"),
            ])
          False ->
            case is_importing {
              True ->
                html.span([attribute.class("badge badge-info")], [
                  html.text("Importing..."),
                ])
              False ->
                html.button(
                  [
                    attribute.class("btn btn-sm btn-primary"),
                    event.on_click(store.ImportPacsStudy(
                      ps.study.study_instance_uid,
                      patient_id,
                    )),
                  ],
                  [html.text("Add")],
                )
            }
        },
      ]),
    ])

  // Series detail rows
  let series_rows =
    list.map(ps.series, fn(s) { pacs_series_row(s) })

  [study_row, ..series_rows]
}

fn pacs_series_row(s: PacsSeriesResult) -> Element(Msg) {
  let description = option.unwrap(s.series_description, "No description")
  let modality = option.unwrap(s.modality, "-")
  let image_count = case s.number_of_series_related_instances {
    Some(n) -> int.to_string(n) <> " images"
    None -> "-"
  }

  html.tr([attribute.class("series-detail-row")], [
    html.td([], []),
    html.td([], [html.text(modality)]),
    html.td([attribute.attribute("colspan", "2")], [html.text(description)]),
    html.td([], [html.text(image_count)]),
  ])
}

fn format_dicom_date(date: Option(String)) -> String {
  case date {
    None -> "-"
    Some(d) ->
      case string.length(d) {
        8 -> {
          let year = string.slice(d, 0, 4)
          let month = string.slice(d, 4, 2)
          let day = string.slice(d, 6, 2)
          year <> "-" <> month <> "-" <> day
        }
        _ -> d
      }
  }
}

fn loading_view(patient_id: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading patient " <> patient_id <> "...")]),
  ])
}
