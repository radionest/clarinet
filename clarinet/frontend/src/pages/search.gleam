// Global search page — self-contained MVU module
import api/models
import api/search.{type SearchResults}
import api/types.{type ApiError, AuthError}
import clarinet_frontend/i18n.{type Key}
import components/entity_link
import gleam/int
import gleam/javascript/promise
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import shared.{type OutMsg, type Shared}

pub type Model {
  Model(query: String, results: Option(SearchResults), loading: Bool)
}

pub type Msg {
  QueryInput(String)
  Submit
  SearchLoaded(Result(SearchResults, ApiError))
}

pub fn init(q: String, _shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  case q {
    "" -> #(Model(query: "", results: None, loading: False), effect.none(), [])
    _ -> #(Model(query: q, results: None, loading: True), run_search(q), [])
  }
}

pub fn update(
  model: Model,
  msg: Msg,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    QueryInput(v) -> #(Model(..model, query: v), effect.none(), [])
    Submit ->
      #(
        Model(..model, results: None, loading: True),
        run_search(model.query),
        [],
      )
    SearchLoaded(Ok(results)) ->
      #(Model(..model, results: Some(results), loading: False), effect.none(), [])
    SearchLoaded(Error(AuthError(_))) ->
      #(
        Model(..model, results: None, loading: False),
        effect.none(),
        [shared.Logout],
      )
    SearchLoaded(Error(_err)) ->
      #(
        Model(..model, results: None, loading: False),
        effect.none(),
        [shared.ShowError(shared.translate(i18n.SearchFailed))],
      )
  }
}

fn run_search(q: String) -> Effect(Msg) {
  effect.from(fn(dispatch) {
    search.search(q, 25)
    |> promise.tap(fn(result) { dispatch(SearchLoaded(result)) })
    Nil
  })
}

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  let t = shared.translate
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text(t(i18n.SearchTitle))]),
    ]),
    search_form(model, t),
    results(model, t),
  ])
}

fn search_form(model: Model, t: fn(Key) -> String) -> Element(Msg) {
  html.form(
    [attribute.class("search-form"), event.on_submit(fn(_) { Submit })],
    [
      html.input([
        attribute.class("form-control"),
        attribute.type_("search"),
        attribute.placeholder(t(i18n.NavSearchPlaceholder)),
        attribute.value(model.query),
        event.on_input(QueryInput),
      ]),
      html.button(
        [attribute.class("btn btn-primary"), attribute.disabled(model.loading)],
        [html.text(t(i18n.BtnSearch))],
      ),
    ],
  )
}

fn results(model: Model, t: fn(Key) -> String) -> Element(Msg) {
  case model.loading, model.results {
    True, _ -> html.p([attribute.class("text-muted")], [html.text(t(i18n.LblLoading))])
    False, None -> html.text("")
    False, Some(res) ->
      case res.patients, res.studies {
        [], [] ->
          html.p([attribute.class("text-muted")], [html.text(t(i18n.SearchNoResults))])
        _, _ ->
          html.div([], [
            section_header(t(i18n.PatientsTitle), list.length(res.patients)),
            html.ul([], list.map(res.patients, patient_item)),
            section_header(t(i18n.StudiesTitle), list.length(res.studies)),
            html.ul([], list.map(res.studies, study_item)),
          ])
      }
  }
}

fn section_header(title: String, count: Int) -> Element(Msg) {
  html.h2([], [html.text(title <> " (" <> int.to_string(count) <> ")")])
}

fn patient_item(p: models.Patient) -> Element(Msg) {
  html.li([], [
    entity_link.patient(p.id),
    html.text(" — " <> option.unwrap(p.name, "")),
  ])
}

fn study_item(s: models.Study) -> Element(Msg) {
  html.li([], [
    entity_link.study_labeled(s.study_uid, entity_link.study_title(s)),
    case s.patient {
      Some(p) -> html.text(" — " <> p.id)
      None -> html.text("")
    },
  ])
}
