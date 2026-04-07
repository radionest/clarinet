// Home/Dashboard page — self-contained MVU module
import api/models
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import router
import shared.{type OutMsg, type Shared}

// --- Model ---

pub type Model {
  Model
}

// --- Msg ---

pub type Msg {
  NoOp
}

// --- Init ---

pub fn init(shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let out_msgs = case shared.user {
    Some(models.User(is_superuser: True, ..)) ->
      [shared.ReloadStudies, shared.ReloadRecords, shared.ReloadUsers]
    Some(_) ->
      [shared.ReloadRecords]
    None -> []
  }
  #(Model, effect.none(), out_msgs)
}

// --- Update ---

pub fn update(
  model: Model,
  _msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  #(model, effect.none(), [])
}

// --- View ---

pub fn view(_model: Model, shared: Shared) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Dashboard")]),
    case shared.user {
      Some(user) -> {
        html.div([attribute.class("dashboard-content")], [
          html.p([attribute.class("welcome")], [
            html.text("Welcome back, " <> user.email <> "!"),
          ]),
          stats_section(shared),
          case user.is_superuser {
            True -> recent_activity(shared)
            False -> html.text("")
          },
        ])
      }
      None -> {
        html.div([attribute.class("welcome-section")], [
          html.h2([], [html.text("Welcome to " <> shared.project_name)]),
          html.p([], [html.text("Please log in to access the dashboard.")]),
          html.a(
            [
              attribute.href(router.route_to_path(router.Login)),
              attribute.class("btn btn-primary"),
            ],
            [html.text("Login")],
          ),
        ])
      }
    },
  ])
}

fn stat_card(
  label label: String,
  count count: Int,
  color color: String,
  route route: router.Route,
) -> Element(Msg) {
  html.div([attribute.class("stat-card card stat-" <> color)], [
    html.div([attribute.class("stat-value")], [html.text(int.to_string(count))]),
    html.div([attribute.class("stat-label")], [html.text(label)]),
    html.a(
      [
        attribute.href(router.route_to_path(route)),
        attribute.class("stat-link"),
      ],
      [html.text("View all →")],
    ),
  ])
}

fn stats_section(shared: Shared) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Overview")]),
    html.div(
      [attribute.class("stats-grid")],
      case shared.user {
        Some(models.User(is_superuser: True, ..)) -> [
          stat_card(
            label: "Studies",
            count: dict.size(shared.cache.studies),
            color: "blue",
            route: router.Studies,
          ),
          stat_card(
            label: "Records",
            count: dict.size(shared.cache.records),
            color: "green",
            route: router.Records,
          ),
        ]
        _ -> [
          stat_card(
            label: "My Records",
            count: dict.size(shared.cache.records),
            color: "green",
            route: router.Records,
          ),
        ]
      },
    ),
  ])
}

fn recent_activity(shared: Shared) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Recent Studies")]),
    html.div([attribute.class("recent-list")], [
      case dict.to_list(shared.cache.studies) {
        [] ->
          html.p([attribute.class("empty-state")], [
            html.text("No recent studies found."),
          ])
        studies -> {
          studies
          |> list.take(5)
          |> list.map(fn(pair) {
            let #(_, study) = pair
            study_item(study)
          })
          |> element.fragment
        }
      },
    ]),
  ])
}

fn study_item(study: models.Study) -> Element(Msg) {
  html.div([attribute.class("recent-item")], [
    html.a(
      [
        attribute.href(
          router.route_to_path(router.StudyDetail(study.study_uid)),
        ),
        attribute.class("recent-link"),
      ],
      [
        html.span([attribute.class("recent-title")], [
          html.text(study.study_uid),
        ]),
      ],
    ),
    html.span([attribute.class("recent-date")], [html.text(study.date)]),
  ])
}
