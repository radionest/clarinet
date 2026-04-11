// Patient detail page — self-contained MVU module
import api/dicom
import api/models.{
  type PacsSeriesResult, type PacsStudyWithSeries, type Patient, type Record,
  type Study,
}
import api/patients
import api/types.{type ApiError, AuthError}
import clarinet_frontend/i18n.{type Key}
import components/forms/base
import components/status_badge
import gleam/dict.{type Dict}
import gleam/int
import gleam/javascript/promise
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}
import utils/load_status.{type LoadStatus}
import utils/record_filters

// --- Model ---

pub type Model {
  Model(
    patient_id: String,
    patient_load_status: LoadStatus,
    pacs_studies: List(PacsStudyWithSeries),
    pacs_loading: Bool,
    pacs_importing: Option(String),
    active_filters: Dict(String, String),
  )
}

// --- Msg ---

pub type Msg {
  // Load
  PatientLoaded(Result(Patient, ApiError))
  RetryLoad
  // Patient actions
  Anonymize
  AnonymizeResult(Result(Patient, ApiError))
  Delete
  DeleteResult(Result(Nil, ApiError))
  // PACS operations
  SearchPacs
  PacsLoaded(Result(List(PacsStudyWithSeries), ApiError))
  ImportPacs(study_uid: String)
  PacsImported(Result(Study, ApiError))
  ClearPacs
  // Records filter
  AddFilter(key: String, value: String)
  RemoveFilter(key: String)
  ClearFilters
  // Navigation
  NavigateBack
  RequestDelete
}

// --- Init ---

pub fn init(
  patient_id: String,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(
      patient_id: patient_id,
      patient_load_status: load_status.Loading,
      pacs_studies: [],
      pacs_loading: False,
      pacs_importing: None,
      active_filters: dict.new(),
    )
  #(model, load_patient_effect(patient_id), [shared.ReloadRecords])
}

fn load_patient_effect(patient_id: String) -> Effect(Msg) {
  use dispatch <- effect.from
  patients.get_patient(patient_id)
  |> promise.tap(fn(result) { dispatch(PatientLoaded(result)) })
  Nil
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    PatientLoaded(Ok(patient)) -> #(
      Model(..model, patient_load_status: load_status.Loaded),
      effect.none(),
      [shared.CachePatient(patient)],
    )

    PatientLoaded(Error(err)) -> #(
      Model(
        ..model,
        patient_load_status: load_status.Failed("Failed to load patient"),
      ),
      effect.none(),
      handle_error(err, "Failed to load patient"),
    )

    RetryLoad -> #(
      Model(..model, patient_load_status: load_status.Loading),
      load_patient_effect(model.patient_id),
      [],
    )

    Anonymize -> {
      let eff = {
        use dispatch <- effect.from
        patients.anonymize_patient(model.patient_id)
        |> promise.tap(fn(result) { dispatch(AnonymizeResult(result)) })
        Nil
      }
      #(model, eff, [shared.SetLoading(True)])
    }

    AnonymizeResult(Ok(patient)) -> #(model, effect.none(), [
      shared.SetLoading(False),
      shared.CachePatient(patient),
      shared.ShowSuccess("Patient anonymized successfully"),
    ])

    AnonymizeResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, "Failed to anonymize patient"),
    )

    Delete -> {
      let eff = {
        use dispatch <- effect.from
        patients.delete_patient(model.patient_id)
        |> promise.tap(fn(result) { dispatch(DeleteResult(result)) })
        Nil
      }
      #(model, eff, [shared.SetLoading(True)])
    }

    DeleteResult(Ok(_)) -> #(model, effect.none(), [
      shared.SetLoading(False),
      shared.ReloadPatients,
      shared.ShowSuccess("Patient deleted successfully"),
      shared.Navigate(router.Patients),
    ])

    DeleteResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, "Failed to delete patient"),
    )

    SearchPacs -> {
      let eff = {
        use dispatch <- effect.from
        dicom.search_patient_studies(model.patient_id)
        |> promise.tap(fn(result) { dispatch(PacsLoaded(result)) })
        Nil
      }
      #(Model(..model, pacs_loading: True), eff, [])
    }

    PacsLoaded(Ok(studies)) -> #(
      Model(..model, pacs_studies: studies, pacs_loading: False),
      effect.none(),
      [],
    )

    PacsLoaded(Error(err)) -> #(
      Model(..model, pacs_loading: False),
      effect.none(),
      handle_error(err, "Failed to search PACS"),
    )

    ImportPacs(study_uid) -> {
      let eff = {
        use dispatch <- effect.from
        dicom.import_study(study_uid, model.patient_id)
        |> promise.tap(fn(result) { dispatch(PacsImported(result)) })
        Nil
      }
      #(Model(..model, pacs_importing: Some(study_uid)), eff, [])
    }

    PacsImported(Ok(study)) -> {
      let updated_pacs =
        list.map(model.pacs_studies, fn(ps) {
          case ps.study.study_instance_uid == study.study_uid {
            True -> models.PacsStudyWithSeries(..ps, already_exists: True)
            False -> ps
          }
        })
      #(
        Model(..model, pacs_importing: None, pacs_studies: updated_pacs),
        effect.none(),
        [
          shared.CacheStudy(study),
          shared.ShowSuccess("Study imported from PACS successfully"),
          shared.ReloadPatient(model.patient_id),
        ],
      )
    }

    PacsImported(Error(err)) -> #(
      Model(..model, pacs_importing: None),
      effect.none(),
      handle_error(err, "Failed to import study from PACS"),
    )

    ClearPacs -> #(
      Model(
        ..model,
        pacs_studies: [],
        pacs_loading: False,
        pacs_importing: None,
      ),
      effect.none(),
      [],
    )

    AddFilter(key, value) -> {
      let filters = dict.insert(model.active_filters, key, value)
      #(Model(..model, active_filters: filters), effect.none(), [])
    }

    RemoveFilter(key) -> {
      let filters = dict.delete(model.active_filters, key)
      #(Model(..model, active_filters: filters), effect.none(), [])
    }

    ClearFilters -> #(
      Model(..model, active_filters: dict.new()),
      effect.none(),
      [],
    )

    NavigateBack -> #(model, effect.none(), [shared.Navigate(router.Patients)])

    RequestDelete -> #(model, effect.none(), [
      shared.OpenDeleteConfirm("patient", model.patient_id),
    ])
  }
}

// --- Helpers ---

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  load_status.render(
    model.patient_load_status,
    fn() { loading_view(model.patient_id) },
    fn() {
      case dict.get(shared.cache.patients, model.patient_id) {
        Ok(patient) -> render_detail(model, shared, patient)
        Error(_) -> loading_view(model.patient_id)
      }
    },
    fn(msg) { error_view(msg) },
  )
}

fn render_detail(model: Model, shared: Shared, patient: Patient) -> Element(Msg) {
  let patient_records =
    dict.values(shared.cache.records)
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
            event.on_click(NavigateBack),
          ],
          [html.text("Back to Patients")],
        ),
        html.button(
          [
            attribute.class("btn btn-danger"),
            event.on_click(RequestDelete),
          ],
          [html.text("Delete Patient")],
        ),
      ]),
    ]),
    patient_info_card(patient),
    studies_section(patient.studies),
    pacs_section(model, patient),
    records_section(model, patient_records, shared.translate),
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
            event.on_click(Anonymize),
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
  let description = option.unwrap(study.study_description, "-")
  let #(modalities, series_count) = case study.series {
    Some(series_list) -> {
      let mods =
        series_list
        |> list.filter_map(fn(s) { option.to_result(s.modality, Nil) })
        |> list.unique
      let mods_str = case mods {
        [] -> "-"
        _ -> string.join(mods, "/")
      }
      #(mods_str, int.to_string(list.length(series_list)))
    }
    None -> #("-", "0")
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
          attribute.href(
            router.route_to_path(router.StudyDetail(study.study_uid)),
          ),
          attribute.class("btn btn-sm btn-outline"),
        ],
        [html.text("View")],
      ),
    ]),
  ])
}

fn records_section(model: Model, records: List(Record), translate: fn(Key) -> String) -> Element(Msg) {
  let filtered = record_filters.apply_filters(records, model.active_filters)

  html.div([attribute.class("card")], [
    html.h3([], [html.text("Records")]),
    case records {
      [] ->
        html.p([attribute.class("text-muted")], [
          html.text("No records found for this patient."),
        ])
      _ ->
        html.div([], [
          records_filter_bar(model, records, translate),
          case filtered {
            [] ->
              html.p([attribute.class("text-muted")], [
                html.text("No records match the current filters."),
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
                  html.tbody([], list.map(filtered, record_row(_, translate))),
                ]),
              ])
          },
        ])
    },
  ])
}

fn records_filter_bar(model: Model, all_records: List(Record), translate: fn(Key) -> String) -> Element(Msg) {
  let status_value =
    dict.get(model.active_filters, "status")
    |> option.from_result()
    |> option.unwrap("")

  let type_value =
    dict.get(model.active_filters, "record_type")
    |> option.from_result()
    |> option.unwrap("")

  let has_filters = !dict.is_empty(model.active_filters)

  html.div([attribute.class("filter-bar")], [
    base.select(
      name: "filter-status",
      value: status_value,
      options: record_filters.status_options(translate),
      on_change: fn(val) {
        case val {
          "" -> RemoveFilter("status")
          _ -> AddFilter("status", val)
        }
      },
    ),
    base.select(
      name: "filter-record-type",
      value: type_value,
      options: record_filters.type_options(all_records, translate),
      on_change: fn(val) {
        case val {
          "" -> RemoveFilter("record_type")
          _ -> AddFilter("record_type", val)
        }
      },
    ),
    case has_filters {
      True ->
        html.button(
          [
            attribute.type_("button"),
            attribute.class("btn btn-sm btn-outline"),
            event.on_click(ClearFilters),
          ],
          [html.text("Clear Filters")],
        )
      False -> html.text("")
    },
  ])
}

fn record_row(record: Record, translate: fn(Key) -> String) -> Element(Msg) {
  let record_id = option.unwrap(record.id, 0)
  let record_id_str = int.to_string(record_id)

  let type_label = case record.record_type {
    Some(rt) -> option.unwrap(rt.label, rt.name)
    None -> record.record_type_name
  }

  html.tr([], [
    html.td([], [html.text(record_id_str)]),
    html.td([], [html.text(type_label)]),
    html.td([], [status_badge.render(record.status, translate)]),
    html.td([], [
      html.a(
        [
          attribute.href(
            router.route_to_path(router.RecordDetail(record_id_str)),
          ),
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
          event.on_click(SearchPacs),
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
              event.on_click(ClearPacs),
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
  _patient_id: String,
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
                    event.on_click(ImportPacs(ps.study.study_instance_uid)),
                  ],
                  [html.text("Add")],
                )
            }
        },
      ]),
    ])

  case ps.series {
    [] -> [study_row]
    series -> {
      let series_details_row =
        html.tr([attribute.class("pacs-series-details-row")], [
          html.td([attribute.attribute("colspan", "5")], [
            element.element(
              "details",
              [attribute.class("pacs-series-details")],
              [
                element.element("summary", [], [
                  html.text("Show series"),
                ]),
                html.table([attribute.class("series-table")], [
                  html.tbody([], list.map(series, fn(s) { pacs_series_row(s) })),
                ]),
              ],
            ),
          ]),
        ])
      [study_row, series_details_row]
    }
  }
}

fn pacs_series_row(s: PacsSeriesResult) -> Element(Msg) {
  let description = option.unwrap(s.series_description, "No description")
  let modality = option.unwrap(s.modality, "-")
  let image_count = case s.number_of_series_related_instances {
    Some(n) -> int.to_string(n) <> " images"
    None -> "-"
  }

  html.tr([attribute.class("series-detail-row")], [
    html.td([], [html.text(modality)]),
    html.td([], [html.text(description)]),
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

fn error_view(message: String) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
    html.button(
      [attribute.class("btn btn-primary"), event.on_click(RetryLoad)],
      [html.text("Retry")],
    ),
  ])
}
