// Home/Dashboard page
import api/models
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Dashboard")]),

    case model.user {
      Some(user) -> {
        html.div([attribute.class("dashboard-content")], [
          html.p([attribute.class("welcome")], [
            html.text("Welcome back, " <> user.username <> "!"),
          ]),
          stats_section(model),
          recent_activity(model),
        ])
      }
      None -> {
        html.div([attribute.class("welcome-section")], [
          html.h2([], [html.text("Welcome to Clarinet")]),
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
  label: String,
  count: Int,
  color: String,
  route: router.Route,
) -> Element(Msg) {
  html.div([attribute.class("stat-card card stat-" <> color)], [
    html.div([attribute.class("stat-value")], [html.text(int.to_string(count))]),
    html.div([attribute.class("stat-label")], [html.text(label)]),
    html.a(
      [
        attribute.href(router.route_to_path(route)),
        attribute.class("stat-link"),
        event.on_click(store.Navigate(route)),
      ],
      [html.text("View all →")],
    ),
  ])
}

fn stats_section(model: Model) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Overview")]),
    html.div([attribute.class("stats-grid")], [
      stat_card("Studies", dict.size(model.studies), "blue", router.Studies),
      stat_card("Tasks", dict.size(model.tasks), "green", router.Tasks),
      stat_card("Users", dict.size(model.users), "purple", router.Users),
    ]),
  ])
}

fn recent_activity(model: Model) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Recent Studies")]),
    html.div([attribute.class("recent-list")], [
      case dict.to_list(model.studies) {
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
        event.on_click(store.Navigate(router.StudyDetail(study.study_uid))),
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
