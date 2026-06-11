// Records list page — self-contained MVU module
import api/models.{type Record, type User}
import api/records
import api/types.{type ApiError, AuthError}
import cache
import cache/bucket
import clarinet_frontend/i18n
import components/records_list
import components/status_badge
import gleam/dict.{type Dict}
import gleam/int
import gleam/javascript/promise
import gleam/option.{None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}
import utils/permissions
import utils/record_filters
import utils/records_list_state
import utils/records_query
import utils/table_sort

// --- Model ---

pub type Model {
  Model(active_filters: Dict(String, String))
}

// --- Msg ---

pub type Msg {
  AddFilter(key: String, value: String)
  RemoveFilter(key: String)
  ClearFilters
  ColumnHeaderClicked(column: String)
  RequestFail(record_id: String)
  Restart(record_id: String)
  RestartResult(Result(Record, ApiError))
}

// --- Init ---

const storage_key = "records.filters"

// The user filter has no visible UI on this page (the dropdown is
// admin-only and lives on /admin), so it must not persist to localStorage —
// a dashboard quick action would otherwise silently resurrect on a later
// plain /records visit. It still round-trips through the URL for deep links.
const transient_filter_keys = ["user"]

pub fn init(
  filters: Dict(String, String),
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let #(effective_filters, init_fx) =
    records_list_state.resolve_initial_filters(
      filters,
      storage_key,
      router.Records,
      transient_filter_keys,
    )
  let key = bucket_key_for(effective_filters, shared.user)
  // The assigned-user column resolves names from `shared.cache.users`,
  // populated by the admin-only `GET /user/`. Load it for admins only so
  // regular users don't trigger a 403 on every visit.
  let base_out = [shared.FetchBucket(key), shared.ReloadFilterOptions]
  let out_msgs = case is_admin(shared.user) {
    True -> [shared.ReloadUsers, ..base_out]
    False -> base_out
  }
  #(Model(active_filters: effective_filters), init_fx, out_msgs)
}

/// Bucket key for the records list. Non-admins see only their own records
/// (the historical `RecordsMine(uid)` scope), admins see all records.
/// For non-admins an explicit `user` filter is resolved by
/// `records_query.scope_for_user`: their own id and the unassigned
/// sentinel are honoured (dashboard quick actions link here with those
/// values), any other id is still clobbered.
fn bucket_key_for(
  filters: Dict(String, String),
  user: option.Option(User),
) -> bucket.BucketKey {
  let base = records_query.from_filters(filters)
  let scoped = case user {
    Some(u) ->
      case permissions.is_admin_user(u) {
        True -> base
        False -> records_query.scope_for_user(base, filters, u.id)
      }
    None -> base
  }
  bucket.Records(scoped)
}

/// The assigned-user column is admin-only: `shared.cache.users` is
/// populated by the admin-only `GET /user/`.
fn is_admin(user: option.Option(User)) -> Bool {
  case user {
    Some(u) -> permissions.is_admin_user(u)
    None -> False
  }
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    AddFilter(key, value) -> {
      let filters = dict.insert(model.active_filters, key, value)
      #(Model(active_filters: filters), sync_filters_effect(filters), [
        shared.FetchBucket(bucket_key_for(filters, shared.user)),
      ])
    }

    RemoveFilter(key) -> {
      let filters = dict.delete(model.active_filters, key)
      #(Model(active_filters: filters), sync_filters_effect(filters), [
        shared.FetchBucket(bucket_key_for(filters, shared.user)),
      ])
    }

    ClearFilters -> {
      // Clearing filters preserves the current sort selection — sorting
      // is independent from filtering and resetting it on every "Clear"
      // would be surprising.
      let filters = record_filters.clear_user_filters(model.active_filters)
      #(Model(active_filters: filters), sync_filters_effect(filters), [
        shared.FetchBucket(bucket_key_for(filters, shared.user)),
      ])
    }

    ColumnHeaderClicked(col) -> {
      let #(cur_col, cur_dir) =
        table_sort.read_sort(
          model.active_filters,
          records_list.default_sort_col,
        )
      let #(new_col, new_dir) = table_sort.next_sort(cur_col, cur_dir, col)
      let new_filters =
        table_sort.write_sort(
          model.active_filters,
          new_col,
          new_dir,
          records_list.default_sort_col,
        )
      #(Model(active_filters: new_filters), sync_filters_effect(new_filters), [
        shared.FetchBucket(bucket_key_for(new_filters, shared.user)),
      ])
    }

    RequestFail(record_id) -> #(model, effect.none(), [
      shared.OpenFailPrompt(record_id),
    ])

    Restart(record_id) -> {
      let eff = {
        use dispatch <- effect.from
        records.restart_record(record_id)
        |> promise.tap(fn(result) { dispatch(RestartResult(result)) })
        Nil
      }
      #(model, eff, [shared.SetLoading(True)])
    }

    RestartResult(Ok(record)) -> #(model, effect.none(), [
      shared.SetLoading(False),
      shared.CacheRecord(record),
      shared.ShowSuccess(shared.translate(i18n.RecordsMsgRestarted)),
      shared.InvalidateAllRecordBuckets,
    ])

    RestartResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, shared.translate(i18n.RecordsMsgRestartFailed)),
    )
  }
}

// --- Helpers ---

fn sync_filters_effect(filters: Dict(String, String)) -> Effect(Msg) {
  records_list_state.sync_filters_effect(
    filters,
    router.Records,
    storage_key,
    transient_filter_keys,
  )
}

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  let t = shared.translate
  let title = case is_admin(shared.user) {
    True -> t(i18n.RecordsAllTitle)
    False -> t(i18n.RecordsTitle)
  }

  let key = bucket_key_for(model.active_filters, shared.user)
  let records = cache.bucket_items(shared.cache, key)
  let status = cache.bucket_status(shared.cache, key)

  html.div([attribute.class("container")], [
    html.h1([], [html.text(title)]),
    records_list.view(
      records,
      status,
      model.active_filters,
      shared,
      list_config(shared),
    ),
  ])
}

/// Build the shared-widget config for the general records list. The
/// assigned-user column appears only for admins (its source cache is the
/// admin-only `GET /user/`); there is no user *filter* here — that lives on
/// the admin dashboard.
fn list_config(shared: Shared) -> records_list.Config(Msg) {
  let show_user = is_admin(shared.user)
  records_list.Config(
    show_type_filter: True,
    show_patient_filter: True,
    show_user_filter: False,
    show_patient_columns: True,
    show_study_series: True,
    show_modality: True,
    empty_message: shared.translate(i18n.RecordsNoFound),
    on_add_filter: AddFilter,
    on_remove_filter: RemoveFilter,
    on_clear_filters: ClearFilters,
    on_column_click: ColumnHeaderClicked,
    status_cell: fn(record) {
      status_badge.render(record.status, shared.translate)
    },
    user_cell: case show_user {
      True -> Some(fn(record) { user_cell_content(shared, record) })
      False -> None
    },
    actions_cell: fn(record) { actions_cell(shared, record) },
  )
}

/// Resolve the assigned-user cell: email from the admin-loaded users
/// cache, falling back to the raw id, or a dash when unassigned.
fn user_cell_content(shared: Shared, record: Record) -> Element(Msg) {
  case record.user_id {
    Some(uid) -> html.text(cache.user_email(shared.cache, uid))
    None -> html.text("—")
  }
}

/// Permission-aware row actions: a primary "Fill" / secondary "Edit" /
/// outline "View" link into the record detail, plus optional Fail/Restart
/// buttons. Returned as a fragment so the widget drops the buttons straight
/// into the actions <td>.
fn actions_cell(shared: Shared, record: Record) -> Element(Msg) {
  let record_id_str = int.to_string(option.unwrap(record.id, 0))
  let can_fill = permissions.can_fill_record(record, shared.user)
  let can_edit = permissions.can_edit_record(record, shared.user)
  let can_fail = permissions.can_fail_record(record, shared.user)
  let can_restart = permissions.can_restart_record(record, shared.user)

  element.fragment([
    case can_fill, can_edit {
      True, _ ->
        records_list.detail_link(
          record,
          "btn btn-sm btn-primary",
          i18n.BtnFill,
          shared.translate,
        )
      _, True ->
        records_list.detail_link(
          record,
          "btn btn-sm btn-secondary",
          i18n.BtnEdit,
          shared.translate,
        )
      _, _ ->
        records_list.detail_link(
          record,
          "btn btn-sm btn-outline",
          i18n.BtnView,
          shared.translate,
        )
    },
    case can_fail {
      True ->
        html.button(
          [
            attribute.class("btn btn-sm btn-danger"),
            event.on_click(RequestFail(record_id_str)),
          ],
          [html.text(shared.translate(i18n.BtnFail))],
        )
      False -> element.none()
    },
    case can_restart {
      True ->
        html.button(
          [
            attribute.class("btn btn-sm btn-warning"),
            event.on_click(Restart(record_id_str)),
          ],
          [html.text(shared.translate(i18n.BtnRestart))],
        )
      False -> element.none()
    },
  ])
}
