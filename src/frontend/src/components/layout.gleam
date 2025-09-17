// Main layout component with navbar and content area
import lustre/element.{type Element}
import lustre/element/html
import lustre/attribute
import lustre/event
import gleam/option.{None, Some}
import gleam/list
import router
import store.{type Model, type Msg}
import api/models

// Main layout view
pub fn view(model: Model, content: Element(Msg)) -> Element(Msg) {
  html.div([attribute.class("app-layout")], [
    navbar(model),
    notifications(model),
    html.main([attribute.class("main-content")], [
      content
    ]),
    footer(),
  ])
}

// Navigation bar
fn navbar(model: Model) -> Element(Msg) {
  html.nav([attribute.class("navbar")], [
    html.div([attribute.class("navbar-brand")], [
      nav_link(router.Home, "Clarinet", model.route)
    ]),
    html.div([attribute.class("navbar-menu")], [
      nav_link(router.Studies, "Studies", model.route),
      nav_link(router.Tasks, "Tasks", model.route),
      case is_admin(model) {
        True -> nav_link(router.Users, "Users", model.route)
        False -> html.text("")
      },
      user_menu(model),
    ]),
  ])
}

// Navigation link
fn nav_link(route: router.Route, text: String, current_route: router.Route) -> Element(Msg) {
  let is_active = router.is_same_section(route, current_route)
  let classes = case is_active {
    True -> "navbar-item active"
    False -> "navbar-item"
  }

  html.a(
    [
      attribute.href(router.route_to_path(route)),
      attribute.class(classes),
      event.on_click(store.Navigate(route)),
    ],
    [html.text(text)]
  )
}

// User menu
fn user_menu(model: Model) -> Element(Msg) {
  case model.user {
    Some(user) -> {
      html.div([attribute.class("navbar-user")], [
        html.span([attribute.class("username")], [html.text(user.username)]),
        html.button(
          [
            attribute.class("btn-logout"),
            event.on_click(store.Logout),
          ],
          [html.text("Logout")]
        ),
      ])
    }
    None -> html.text("")
  }
}

// Notification area for errors and success messages
fn notifications(model: Model) -> Element(Msg) {
  html.div([attribute.class("notifications")], [
    case model.error {
      Some(error) -> error_notification(error)
      None -> html.text("")
    },
    case model.success_message {
      Some(message) -> success_notification(message)
      None -> html.text("")
    },
  ])
}

// Error notification
fn error_notification(message: String) -> Element(Msg) {
  html.div([attribute.class("notification notification-error")], [
    html.span([], [html.text(message)]),
    html.button(
      [
        attribute.class("notification-close"),
        event.on_click(store.ClearError),
      ],
      [html.text("×")]
    ),
  ])
}

// Success notification
fn success_notification(message: String) -> Element(Msg) {
  html.div([attribute.class("notification notification-success")], [
    html.span([], [html.text(message)]),
    html.button(
      [
        attribute.class("notification-close"),
        event.on_click(store.ClearSuccessMessage),
      ],
      [html.text("×")]
    ),
  ])
}

// Footer
fn footer() -> Element(Msg) {
  html.footer([attribute.class("app-footer")], [
    html.div([attribute.class("container")], [
      html.p([], [
        html.text("© 2024 Clarinet Medical Imaging Framework"),
      ]),
    ]),
  ])
}

// Check if user is admin
fn is_admin(model: Model) -> Bool {
  case model.user {
    Some(user) -> {
      // Check if user is superuser or has admin role
      user.is_superuser
    }
    None -> False
  }
}