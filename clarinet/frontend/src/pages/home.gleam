// Home/Dashboard page — self-contained MVU module.
//
// Admins keep the overview (stats + recent studies). Regular users get a
// personal worklist — their records grouped by status (in work / pending /
// paused / finished) — plus a "take a task" picker that claims a record of a
// chosen type from the pool and opens it.
import api/models
import api/records
import api/types.{type ApiError}
import cache
import cache/bucket.{type BucketKey, type BucketStatus}
import clarinet_frontend/i18n
import components/forms/base
import gleam/dict
import gleam/int
import gleam/javascript/promise
import gleam/list
import gleam/option.{None, Some}
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}
import utils/permissions

// --- Model ---

pub type Model {
  Model(
    // Record types the user can still take from the pool, with pending counts.
    pool_types: List(#(String, Int)),
    // False until the first available-types fetch resolves — avoids flashing
    // the empty-pool message before the data arrives.
    pool_loaded: Bool,
    // Selected type in the "take a task" picker ("" = nothing selected).
    selected_type: String,
    // True while a claim request is in flight (disables the button).
    claiming: Bool,
  )
}

fn empty_model() -> Model {
  Model(pool_types: [], pool_loaded: False, selected_type: "", claiming: False)
}

// --- Msg ---

pub type Msg {
  AvailableTypesLoaded(Result(dict.Dict(String, Int), ApiError))
  PoolTypeSelected(String)
  TakeTaskClicked
  TaskClaimed(Result(models.Record, ApiError))
}

// --- Worklist ---

// Worklist groups in display order: backend status string + its localized
// title. Single source for the status set, its order, and its labels — each
// maps to one single-status bucket scoped to the user. "paused" is `pause`.
const worklist_groups: List(#(String, i18n.Key)) = [
  #("inwork", i18n.StatusInProgress),
  #("pending", i18n.StatusPending),
  #("pause", i18n.StatusPaused),
  #("finished", i18n.StatusCompleted),
]

/// Bucket key for the user's records in a single status. `wo_user: Some(False)`
/// pins "strictly assigned to me" despite the backend's include_unassigned
/// widening for regular users (see RecordsQuery docs).
fn worklist_key(user_id: String, status: String) -> BucketKey {
  bucket.Records(
    bucket.RecordsQuery(
      ..bucket.default_query(),
      user_id: Some(user_id),
      record_status: Some(status),
      wo_user: Some(False),
    ),
  )
}

/// Admin overview counts all records (no user/status scope).
fn admin_records_key() -> BucketKey {
  bucket.Records(bucket.default_query())
}

// --- Init ---

pub fn init(shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  case shared.user {
    Some(u) ->
      case permissions.is_admin_user(u) {
        True -> #(empty_model(), effect.none(), [
          shared.ReloadStudies,
          shared.FetchBucket(admin_records_key()),
          shared.ReloadUsers,
        ])
        False -> #(
          empty_model(),
          load_available_types_effect(),
          list.map(worklist_groups, fn(g) {
            shared.FetchBucket(worklist_key(u.id, g.0))
          }),
        )
      }
    None -> #(empty_model(), effect.none(), [])
  }
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    AvailableTypesLoaded(Ok(counts)) -> #(
      Model(..model, pool_types: sorted_pool_types(counts), pool_loaded: True),
      effect.none(),
      [],
    )
    AvailableTypesLoaded(Error(err)) -> #(
      Model(..model, pool_loaded: True),
      effect.none(),
      handle_error(err, shared.translate(i18n.HomeNoPoolTasks)),
    )
    PoolTypeSelected(name) -> #(
      Model(..model, selected_type: name),
      effect.none(),
      [],
    )
    TakeTaskClicked ->
      case model.selected_type {
        "" -> #(model, effect.none(), [])
        name -> #(Model(..model, claiming: True), claim_next_effect(name), [])
      }
    TaskClaimed(Ok(record)) -> #(
      Model(..model, claiming: False),
      effect.none(),
      take_success_out_msgs(record, shared),
    )
    TaskClaimed(Error(err)) -> #(
      Model(..model, claiming: False),
      effect.none(),
      handle_error(err, shared.translate(i18n.HomeTakeTaskError)),
    )
  }
}

/// Drop types with no claimable record, sort by name for a stable dropdown.
fn sorted_pool_types(counts: dict.Dict(String, Int)) -> List(#(String, Int)) {
  counts
  |> dict.to_list
  |> list.filter(fn(pair) { pair.1 > 0 })
  |> list.sort(fn(a, b) { string.compare(a.0, b.0) })
}

/// After a successful claim the record is inwork and assigned to the user —
/// open it so they can start working immediately.
fn take_success_out_msgs(record: models.Record, shared: Shared) -> List(OutMsg) {
  let success = shared.ShowSuccess(shared.translate(i18n.HomeTaskTaken))
  case record.id {
    Some(id) -> [
      success,
      shared.Navigate(router.RecordDetail(int.to_string(id))),
    ]
    None -> [success]
  }
}

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    types.AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

// --- Effects ---

fn load_available_types_effect() -> Effect(Msg) {
  use dispatch <- effect.from
  records.get_available_types()
  |> promise.tap(fn(res) { dispatch(AvailableTypesLoaded(res)) })
  Nil
}

fn claim_next_effect(record_type_name: String) -> Effect(Msg) {
  use dispatch <- effect.from
  records.claim_next(record_type_name)
  |> promise.tap(fn(res) { dispatch(TaskClaimed(res)) })
  Nil
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  let t = shared.translate
  html.div([attribute.class("container")], [
    html.h1([], [html.text(t(i18n.HomeDashboard))]),
    case shared.user {
      Some(user) ->
        html.div([attribute.class("dashboard-content")], [
          html.p([attribute.class("welcome")], [
            html.text(t(i18n.HomeWelcome(user.email))),
          ]),
          case permissions.is_admin_user(user) {
            True -> admin_sections(shared)
            False -> user_sections(model, shared, user)
          },
        ])
      None -> logged_out_view(shared)
    },
  ])
}

fn logged_out_view(shared: Shared) -> Element(Msg) {
  let t = shared.translate
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

// --- Admin sections (overview + recent studies) ---

fn admin_sections(shared: Shared) -> Element(Msg) {
  element.fragment([stats_section(shared), recent_activity(shared)])
}

fn stats_section(shared: Shared) -> Element(Msg) {
  let t = shared.translate
  let records_count =
    list.length(cache.bucket_items(shared.cache, admin_records_key()))
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text(t(i18n.HomeOverview))]),
    html.div([attribute.class("stats-grid")], [
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
    ]),
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

// --- User sections (take a task + worklist) ---

fn user_sections(
  model: Model,
  shared: Shared,
  user: models.User,
) -> Element(Msg) {
  element.fragment([
    take_task_section(model, shared),
    worklist_section(shared, user),
  ])
}

/// "Take a task" picker: a type dropdown (with pool counts) + a claim button.
/// Hidden when the pool holds nothing the user may take.
fn take_task_section(model: Model, shared: Shared) -> Element(Msg) {
  let t = shared.translate
  let body = case model.pool_loaded, model.pool_types {
    False, _ ->
      html.p([attribute.class("loading-indicator")], [
        html.text(t(i18n.LblLoading)),
      ])
    True, [] ->
      html.p([attribute.class("empty-state")], [
        html.text(t(i18n.HomeNoPoolTasks)),
      ])
    True, types_list ->
      html.div([attribute.class("take-task")], [
        base.select(
          name: "take-task-type",
          value: model.selected_type,
          options: pool_type_options(types_list, shared),
          on_change: PoolTypeSelected,
        ),
        html.button(
          [
            attribute.type_("button"),
            attribute.class("btn btn-primary"),
            attribute.disabled(model.selected_type == "" || model.claiming),
            event.on_click(TakeTaskClicked),
          ],
          [html.text(t(i18n.HomeTakeTask))],
        ),
      ])
  }
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text(t(i18n.HomeTakeTask))]),
    body,
  ])
}

fn pool_type_options(
  types_list: List(#(String, Int)),
  shared: Shared,
) -> List(#(String, String)) {
  [
    #("", shared.translate(i18n.HomeTakeTaskPlaceholder)),
    ..list.map(types_list, fn(pair) {
      let #(name, count) = pair
      #(name, type_label(name, shared) <> " (" <> int.to_string(count) <> ")")
    })
  ]
}

fn worklist_section(shared: Shared, user: models.User) -> Element(Msg) {
  let t = shared.translate
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text(t(i18n.HomeMyTasks))]),
    html.div(
      [attribute.class("worklist")],
      list.map(worklist_groups, fn(g) { worklist_group(g.0, g.1, shared, user) }),
    ),
  ])
}

fn worklist_group(
  status_str: String,
  title_key: i18n.Key,
  shared: Shared,
  user: models.User,
) -> Element(Msg) {
  let key = worklist_key(user.id, status_str)
  let items = cache.bucket_items(shared.cache, key)
  let status = cache.bucket_status(shared.cache, key)
  html.div([attribute.class("worklist-group")], [
    html.h4([attribute.class("worklist-group-title")], [
      html.text(
        shared.translate(title_key)
        <> " ("
        <> int.to_string(list.length(items))
        <> ")",
      ),
    ]),
    worklist_group_body(status, items, status_str, shared, user),
  ])
}

fn worklist_group_body(
  status: BucketStatus,
  items: List(models.Record),
  status_str: String,
  shared: Shared,
  user: models.User,
) -> Element(Msg) {
  case status {
    bucket.Cold | bucket.Loading ->
      html.p([attribute.class("loading-indicator")], [
        html.text(shared.translate(i18n.LblLoading)),
      ])
    bucket.Failed(err) ->
      html.p([attribute.class("text-error")], [html.text(err)])
    _ ->
      case items {
        [] ->
          html.p([attribute.class("empty-state")], [
            html.text(shared.translate(i18n.HomeWorklistEmpty)),
          ])
        _ ->
          element.fragment([
            html.ul(
              [attribute.class("worklist-items")],
              list.map(items, fn(r) { worklist_item(r, shared) }),
            ),
            html.a(
              [
                attribute.href(
                  router.route_to_href(records_filter_route(status_str, user)),
                ),
                attribute.class("worklist-show-all"),
              ],
              [html.text(shared.translate(i18n.HomeViewAll))],
            ),
          ])
      }
  }
}

fn worklist_item(record: models.Record, shared: Shared) -> Element(Msg) {
  let id_str = int.to_string(option.unwrap(record.id, 0))
  html.li([attribute.class("worklist-item")], [
    html.span([attribute.class("worklist-item-id")], [html.text("#" <> id_str)]),
    html.span([attribute.class("worklist-item-type")], [
      html.text(record_type_label(record)),
    ]),
    html.span([attribute.class("worklist-item-patient")], [
      html.text(record.patient_id),
    ]),
    html.a(
      [
        attribute.href(router.route_to_path(router.RecordDetail(id_str))),
        attribute.class("worklist-item-open"),
      ],
      [html.text(shared.translate(i18n.HomeOpenTask))],
    ),
  ])
}

// --- Helpers ---

fn records_filter_route(status_str: String, user: models.User) -> router.Route {
  router.Records(dict.from_list([#("status", status_str), #("user", user.id)]))
}

/// Pool-picker label: the record type's display label, falling back to its name
/// (the available-types endpoint only returns names; labels come from cache).
fn type_label(name: String, shared: Shared) -> String {
  case dict.get(shared.cache.record_types, name) {
    Ok(rt) -> option.unwrap(rt.label, name)
    Error(_) -> name
  }
}

fn record_type_label(record: models.Record) -> String {
  case record.record_type {
    Some(rt) -> option.unwrap(rt.label, rt.name)
    None -> record.record_type_name
  }
}
