// Home/Dashboard page — self-contained MVU module
import api/models
import cache
import cache/bucket
import clarinet_frontend/i18n
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
import utils/permissions
import utils/record_filters

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
  let bucket_key = home_bucket_key(shared.user)
  let out_msgs = case shared.user {
    Some(u) ->
      case permissions.is_admin_user(u) {
        True -> [
          shared.ReloadStudies,
          shared.FetchBucket(bucket_key),
          shared.ReloadUsers,
        ]
        False -> [shared.FetchBucket(bucket_key)]
      }
    None -> []
  }
  #(Model, effect.none(), out_msgs)
}

fn home_bucket_key(user: option.Option(models.User)) -> bucket.BucketKey {
  let base = bucket.default_query()
  let q = case user {
    Some(u) ->
      case permissions.is_admin_user(u) {
        True -> base
        False -> bucket.RecordsQuery(..base, user_id: Some(u.id))
      }
    None -> base
  }
  bucket.Records(q)
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
  let t = shared.translate
  html.div([attribute.class("container")], [
    html.h1([], [html.text(t(i18n.HomeDashboard))]),
    case shared.user {
      Some(user) -> {
        html.div([attribute.class("dashboard-content")], [
          html.p([attribute.class("welcome")], [
            html.text(t(i18n.HomeWelcome(user.email))),
          ]),
          stats_section(shared),
          case permissions.is_admin_user(user) {
            True -> recent_activity(shared)
            False -> quick_actions_section(shared, user)
          },
        ])
      }
      None -> {
        html.div([attribute.class("welcome-section")], [
          html.h2([], [html.text(t(i18n.HomeWelcomeTo(shared.project_name)))]),
          html.p([], [html.text(t(i18n.HomeLoginPrompt))]),
          html.a(
            [
              attribute.href(router.route_to_path(router.Login)),
              attribute.class("btn btn-primary"),
            ],
            [html.text(t(i18n.BtnLogin))],
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
  link_text link_text: String,
) -> Element(Msg) {
  html.div([attribute.class("stat-card card stat-" <> color)], [
    html.div([attribute.class("stat-value")], [html.text(int.to_string(count))]),
    html.div([attribute.class("stat-label")], [html.text(label)]),
    html.a(
      [
        attribute.href(router.route_to_path(route)),
        attribute.class("stat-link"),
      ],
      [html.text(link_text)],
    ),
  ])
}

fn stats_section(shared: Shared) -> Element(Msg) {
  let t = shared.translate
  // Resolved once and reused for both admin and non-admin stat cards.
  // home_bucket_key returns an admin-wide query for admins and a
  // user-scoped one for everyone else, so the same value is correct
  // in either branch below — don't inline-duplicate the call.
  let records_bucket_key = home_bucket_key(shared.user)
  let records_count =
    list.length(cache.bucket_items(shared.cache, records_bucket_key))
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text(t(i18n.HomeOverview))]),
    html.div(
      [attribute.class("stats-grid")],
      case shared.user {
        Some(u) ->
          case permissions.is_admin_user(u) {
            True -> [
              stat_card(
                label: t(i18n.HomeStudies),
                count: dict.size(shared.cache.studies),
                color: "blue",
                route: router.Studies(dict.new()),
                link_text: t(i18n.HomeViewAll),
              ),
              stat_card(
                label: t(i18n.HomeRecords),
                count: records_count,
                color: "green",
                route: router.Records(dict.new()),
                link_text: t(i18n.HomeViewAll),
              ),
            ]
            False -> [
              stat_card(
                label: t(i18n.HomeMyRecords),
                count: records_count,
                color: "green",
                route: router.Records(dict.new()),
                link_text: t(i18n.HomeViewAll),
              ),
            ]
          }
        None -> []
      },
    ),
  ])
}

/// One-click entry points into the user's worklist: records they are
/// working on, pending records assigned to them, and free pending records
/// they could claim — without touching the filter bar on /records.
fn quick_actions_section(shared: Shared, user: models.User) -> Element(Msg) {
  let t = shared.translate
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text(t(i18n.HomeQuickActions))]),
    html.div([attribute.class("quick-actions-grid")], [
      quick_action(
        t(i18n.HomeActionInWork),
        "qa-inwork",
        router.Records(
          dict.from_list([#("status", "inwork"), #("user", user.id)]),
        ),
      ),
      quick_action(
        t(i18n.HomeActionMyPending),
        "qa-pending",
        router.Records(
          dict.from_list([#("status", "pending"), #("user", user.id)]),
        ),
      ),
      quick_action(
        t(i18n.HomeActionFreePending),
        "qa-free",
        router.Records(
          dict.from_list([
            #("status", "pending"),
            #("user", record_filters.unassigned_user_value),
          ]),
        ),
      ),
    ]),
  ])
}

fn quick_action(
  label: String,
  modifier: String,
  route: router.Route,
) -> Element(Msg) {
  html.a(
    [
      attribute.href(router.route_to_href(route)),
      attribute.class("quick-action card " <> modifier),
    ],
    [html.text(label)],
  )
}

fn recent_activity(shared: Shared) -> Element(Msg) {
  let t = shared.translate
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text(t(i18n.HomeRecentStudies))]),
    html.div([attribute.class("recent-list")], [
      case dict.to_list(shared.cache.studies) {
        [] ->
          html.p([attribute.class("empty-state")], [
            html.text(t(i18n.HomeNoRecentStudies)),
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
