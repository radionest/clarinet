// Home/Dashboard page
import lustre/element.{type Element}
import lustre/element/html
import lustre/attribute
import lustre/event
import gleam/option.{Some, None}
import gleam/list
import router
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Dashboard")]),

    case model.user {
      Some(user) -> welcome_section(user.username)
      None -> html.text("")
    },

    html.div([attribute.class("dashboard-grid")], [
      stat_card("Studies", list.length(model.studies_list), "primary", router.Studies),
      stat_card("Tasks", list.length(model.tasks_list), "success", router.Tasks),
      stat_card("Active Tasks", count_active_tasks(model.tasks_list), "warning", router.Tasks),
      stat_card("Users", list.length(model.users_list), "info", router.Users),
    ]),

    html.div([attribute.class("dashboard-sections")], [
      recent_studies_section(model),
      recent_tasks_section(model),
    ]),
  ])
}

fn welcome_section(username: String) -> Element(Msg) {
  html.div([attribute.class("welcome-section")], [
    html.h2([], [html.text("Welcome back, " <> username <> "!")]),
    html.p([attribute.class("text-muted")], [
      html.text("Here's an overview of your medical imaging workspace.")
    ]),
  ])
}

fn stat_card(title: String, count: Int, color: String, route: router.Route) -> Element(Msg) {
  html.div([attribute.class("stat-card card stat-" <> color)], [
    html.div([attribute.class("stat-value")], [html.text(int.to_string(count))]),
    html.div([attribute.class("stat-label")], [html.text(title)]),
    html.a(
      [
        attribute.href(router.route_to_path(route)),
        attribute.class("stat-link"),
        event.on_click(fn(_) {
          event.prevent_default()
          store.Navigate(route)
        }),
      ],
      [html.text("View all →")]
    ),
  ])
}

fn recent_studies_section(model: Model) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.div([attribute.class("section-header")], [
      html.h3([], [html.text("Recent Studies")]),
      html.a(
        [
          attribute.href("/studies"),
          attribute.class("btn btn-outline btn-small"),
          event.on_click(fn(_) {
            event.prevent_default()
            store.Navigate(router.Studies)
          }),
        ],
        [html.text("View All")]
      ),
    ]),
    case model.studies_list {
      [] -> html.p([attribute.class("text-muted")], [html.text("No studies found")])
      studies -> {
        let recent = list.take(studies, 5)
        html.div([attribute.class("recent-list")], [
          list.map(recent, study_item)
          |> html.fragment()
        ])
      }
    },
  ])
}

fn study_item(study: models.Study) -> Element(Msg) {
  html.div([attribute.class("recent-item")], [
    html.div([attribute.class("recent-item-main")], [
      html.a(
        [
          attribute.href("/studies/" <> get_study_id(study)),
          attribute.class("recent-item-title"),
          event.on_click(fn(_) {
            event.prevent_default()
            store.Navigate(router.StudyDetail(get_study_id(study)))
          }),
        ],
        [html.text(study.description)]
      ),
      html.span([attribute.class("text-muted")], [
        html.text(study.modality <> " • " <> study.study_date)
      ]),
    ]),
    html.div([attribute.class("recent-item-meta")], [
      html.span([attribute.class("badge")], [
        html.text(int.to_string(study.series_count) <> " series")
      ]),
    ]),
  ])
}

fn recent_tasks_section(model: Model) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.div([attribute.class("section-header")], [
      html.h3([], [html.text("Recent Tasks")]),
      html.a(
        [
          attribute.href("/tasks"),
          attribute.class("btn btn-outline btn-small"),
          event.on_click(fn(_) {
            event.prevent_default()
            store.Navigate(router.Tasks)
          }),
        ],
        [html.text("View All")]
      ),
    ]),
    case model.tasks_list {
      [] -> html.p([attribute.class("text-muted")], [html.text("No tasks found")])
      tasks -> {
        let recent = list.take(tasks, 5)
        html.div([attribute.class("recent-list")], [
          list.map(recent, task_item)
          |> html.fragment()
        ])
      }
    },
  ])
}

fn task_item(task: models.Task) -> Element(Msg) {
  html.div([attribute.class("recent-item")], [
    html.div([attribute.class("recent-item-main")], [
      html.a(
        [
          attribute.href("/tasks/" <> get_task_id(task)),
          attribute.class("recent-item-title"),
          event.on_click(fn(_) {
            event.prevent_default()
            store.Navigate(router.TaskDetail(get_task_id(task)))
          }),
        ],
        [html.text(get_task_name(task))]
      ),
      html.span([attribute.class("text-muted")], [
        html.text(format_task_date(task))
      ]),
    ]),
    html.div([attribute.class("recent-item-meta")], [
      task_status_badge(task.status),
    ]),
  ])
}

fn task_status_badge(status: models.TaskStatus) -> Element(Msg) {
  let #(text, class) = case status {
    models.Pending -> #("Pending", "badge-secondary")
    models.Running -> #("Running", "badge-primary")
    models.Completed -> #("Completed", "badge-success")
    models.Failed -> #("Failed", "badge-danger")
    models.Cancelled -> #("Cancelled", "badge-warning")
  }
  html.span([attribute.class("badge " <> class)], [html.text(text)])
}

// Helper functions
fn count_active_tasks(tasks: List(models.Task)) -> Int {
  list.filter(tasks, fn(task) {
    case task.status {
      models.Running | models.Pending -> True
      _ -> False
    }
  })
  |> list.length()
}

fn get_study_id(study: models.Study) -> String {
  case study.id {
    Some(id) -> int.to_string(id)
    None -> ""
  }
}

fn get_task_id(task: models.Task) -> String {
  case task.id {
    Some(id) -> int.to_string(id)
    None -> ""
  }
}

fn get_task_name(task: models.Task) -> String {
  case task.design {
    Some(design) -> design.name
    None -> "Task #" <> get_task_id(task)
  }
}

fn format_task_date(task: models.Task) -> String {
  case task.created_at {
    Some(date) -> format_date(date)
    None -> ""
  }
}

// JavaScript FFI for date formatting
@external(javascript, "../ffi/utils.js", "formatDate")
fn format_date(date_string: String) -> String