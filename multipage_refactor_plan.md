# План рефакторинга Gleam/Lustre приложения на модульную архитектуру

## Обзор текущей архитектуры

### Проблемы текущей реализации:
1. **Монолитный store.Msg** - содержит 70+ вариантов сообщений для всех страниц
2. **Единая глобальная Model** - хранит состояние всех страниц в одном месте
3. **Сложность поддержки** - добавление новых страниц увеличивает сложность
4. **Отсутствие изоляции** - изменения в одной странице могут затронуть другие
5. **Низкая переиспользуемость** - страницы тесно связаны с глобальным состоянием

## Предлагаемая архитектура

### Основные принципы:
- **Модульность**: Каждая страница - независимый модуль
- **Композиция**: Страницы композируются в главное приложение
- **Изоляция**: Каждая страница имеет свои типы и логику
- **Переиспользуемость**: Страницы можно легко переносить между проектами

### Новая структура файлов:

```
src/frontend/src/
├── core/                       # Ядро приложения
│   ├── store.gleam            # Глобальное состояние и оркестрация
│   ├── messages.gleam         # Иерархия сообщений
│   ├── shared.gleam           # Общие типы (User, Cache, SharedContext)
│   └── effects.gleam          # Композиция эффектов
│
├── pages/                      # Модульные страницы
│   ├── auth/
│   │   ├── login.gleam        # Model, Msg, init, update, view
│   │   └── register.gleam
│   │
│   ├── studies/
│   │   ├── list.gleam         # Список исследований
│   │   ├── create.gleam       # Создание исследования
│   │   └── detail.gleam       # Детали исследования
│   │
│   ├── patients/
│   │   ├── list.gleam         # Список пациентов
│   │   ├── create.gleam       # Добавление пациента
│   │   └── detail.gleam       # Карточка пациента
│   │
│   ├── tasks/
│   │   ├── list.gleam         # Список задач
│   │   ├── create.gleam       # Создание задачи
│   │   ├── execute.gleam      # Выполнение задачи
│   │   └── resolve.gleam      # Решение задачи
│   │
│   ├── admin/
│   │   ├── dashboard.gleam    # Админ-панель
│   │   ├── settings.gleam     # Настройки системы
│   │   └── users.gleam        # Управление пользователями
│   │
│   └── home.gleam             # Главная страница
│
├── components/                 # Переиспользуемые компоненты
│   ├── layout.gleam
│   ├── navigation.gleam
│   └── forms/
│       └── user_form.gleam
│
└── main.gleam                  # Точка входа приложения
```

## Детальная реализация

### 1. Иерархия сообщений (core/messages.gleam)

```gleam
// core/messages.gleam
import pages/auth/login
import pages/auth/register
import pages/studies/list as studies_list
import pages/studies/create as studies_create
import pages/studies/detail as studies_detail
import pages/patients/list as patients_list
import pages/patients/create as patients_create
import pages/tasks/list as tasks_list
import pages/tasks/create as tasks_create
import pages/tasks/execute as tasks_execute
import pages/tasks/resolve as tasks_resolve
import pages/admin/dashboard as admin_dashboard
import pages/admin/settings as admin_settings
import pages/home

// Глобальные сообщения
pub type GlobalMsg {
  // Навигация
  RouteChanged(Route)
  NavigateTo(Route)

  // Аутентификация (глобальная)
  UserLoggedIn(User)
  UserLoggedOut
  SessionExpired
  RefreshSession

  // Уведомления (глобальные)
  ShowNotification(String, NotificationType)
  HideNotification

  // Управление кешем
  CacheSet(String, Dynamic)
  CacheInvalidate(String)
  CacheClear

  // Системные события
  NetworkError(String)
  ApiError(ApiError)
}

// Сообщения страниц (обертки)
pub type PageMsg {
  // Аутентификация
  LoginMsg(login.Msg)
  RegisterMsg(register.Msg)

  // Исследования
  StudiesListMsg(studies_list.Msg)
  StudiesCreateMsg(studies_create.Msg)
  StudiesDetailMsg(studies_detail.Msg)

  // Пациенты
  PatientsListMsg(patients_list.Msg)
  PatientsCreateMsg(patients_create.Msg)

  // Задачи
  TasksListMsg(tasks_list.Msg)
  TasksCreateMsg(tasks_create.Msg)
  TasksExecuteMsg(tasks_execute.Msg)
  TasksResolveMsg(tasks_resolve.Msg)

  // Админка
  AdminDashboardMsg(admin_dashboard.Msg)
  AdminSettingsMsg(admin_settings.Msg)

  // Главная
  HomeMsg(home.Msg)
}

// Основной тип сообщений приложения
pub type Msg {
  Global(GlobalMsg)
  Page(PageMsg)
}
```

### 2. Общий контекст (core/shared.gleam)

```gleam
// core/shared.gleam
import gleam/dict.{type Dict}
import gleam/option.{type Option}
import api/models.{type User}
import lustre/effect.{type Effect}

// Общий контекст, передаваемый страницам
pub type SharedContext {
  SharedContext(
    user: Option(User),
    cache: Dict(String, Dynamic),
    dispatch_global: fn(GlobalRequest) -> Effect(a)
  )
}

// Запросы от страниц к глобальному состоянию
pub type GlobalRequest {
  // Навигация
  NavigateToRoute(Route)
  NavigateBack

  // Пользователь
  RefreshUserData
  RequireAuth

  // Кеш
  GetCachedData(String)
  SetCachedData(String, Dynamic)
  InvalidateCachedData(String)

  // Уведомления
  ShowNotification(String, NotificationType)
  ShowError(String)
  ShowSuccess(String)

  // API
  MakeApiRequest(ApiRequest)
}

pub type NotificationType {
  Info
  Success
  Warning
  Error
}

// Общие типы для кеша
pub type CacheEntry {
  CacheEntry(
    key: String,
    data: Dynamic,
    timestamp: Int,
    ttl: Option(Int)  // Time to live в секундах
  )
}
```

### 3. Пример страницы: Список исследований (pages/studies/list.gleam)

```gleam
// pages/studies/list.gleam
import gleam/dict
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/result
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/attribute
import lustre/event
import api/models.{type Study}
import api/studies as api
import core/shared.{type SharedContext, type GlobalRequest}

// Локальная модель страницы
pub type Model {
  Model(
    studies: List(Study),
    loading: Bool,
    error: Option(String),
    search_query: String,
    selected_studies: List(String),
    sort_by: SortField,
    sort_order: SortOrder,
    page: Int,
    per_page: Int,
    total: Int
  )
}

pub type SortField {
  ByDate
  ByPatient
  ByModality
  ByStatus
}

pub type SortOrder {
  Ascending
  Descending
}

// Локальные сообщения страницы
pub type Msg {
  // Загрузка данных
  LoadStudies
  StudiesLoaded(Result(List(Study), String))

  // Поиск и фильтрация
  UpdateSearch(String)
  ApplyFilters
  ClearFilters

  // Сортировка
  SetSortField(SortField)
  ToggleSortOrder

  // Выбор элементов
  SelectStudy(String)
  DeselectStudy(String)
  SelectAll
  DeselectAll

  // Действия
  CreateNew
  ViewDetails(String)
  DeleteSelected
  DeleteConfirmed(Result(List(String), String))
  ExportSelected

  // Пагинация
  NextPage
  PrevPage
  SetPage(Int)
  SetPerPage(Int)

  // Обновление
  RefreshData
}

// Инициализация страницы
pub fn init() -> #(Model, Effect(Msg)) {
  let model = Model(
    studies: [],
    loading: True,
    error: None,
    search_query: "",
    selected_studies: [],
    sort_by: ByDate,
    sort_order: Descending,
    page: 1,
    per_page: 20,
    total: 0
  )

  #(model, load_studies_effect())
}

// Обновление состояния с контекстом
pub fn update(
  model: Model,
  msg: Msg,
  context: SharedContext
) -> #(Model, Effect(Msg)) {
  case msg {
    LoadStudies -> {
      let new_model = Model(..model, loading: True, error: None)

      // Проверяем кеш
      let cache_key = "studies_list_" <> int.to_string(model.page)

      case dict.get(context.cache, cache_key) {
        Ok(cached) -> {
          // Используем кешированные данные
          case decode_studies(cached) {
            Ok(studies) -> {
              #(
                Model(..new_model, studies: studies, loading: False),
                effect.none()
              )
            }
            Error(_) -> {
              // Кеш поврежден, загружаем с сервера
              #(new_model, load_studies_effect())
            }
          }
        }
        Error(_) -> {
          // Нет в кеше, загружаем с сервера
          #(new_model, load_studies_effect())
        }
      }
    }

    StudiesLoaded(Ok(studies)) -> {
      let new_model = Model(
        ..model,
        studies: studies,
        loading: False,
        total: list.length(studies)
      )

      // Сохраняем в кеш
      let cache_key = "studies_list_" <> int.to_string(model.page)
      let cache_effect = context.dispatch_global(
        shared.SetCachedData(cache_key, dynamic.from(studies))
      )

      #(new_model, cache_effect)
    }

    StudiesLoaded(Error(error)) -> {
      #(
        Model(..model, loading: False, error: Some(error)),
        context.dispatch_global(shared.ShowError(error))
      )
    }

    UpdateSearch(query) -> {
      #(Model(..model, search_query: query), effect.none())
    }

    ApplyFilters -> {
      let filtered = filter_studies(model.studies, model.search_query)
      #(Model(..model, studies: filtered), effect.none())
    }

    CreateNew -> {
      // Запрос навигации через глобальный контекст
      #(
        model,
        context.dispatch_global(
          shared.NavigateToRoute(router.StudiesCreate)
        )
      )
    }

    ViewDetails(study_uid) -> {
      #(
        model,
        context.dispatch_global(
          shared.NavigateToRoute(router.StudyDetail(study_uid))
        )
      )
    }

    SelectStudy(uid) -> {
      let selected = [uid, ..model.selected_studies]
      #(Model(..model, selected_studies: selected), effect.none())
    }

    DeselectStudy(uid) -> {
      let selected = list.filter(model.selected_studies, fn(s) { s != uid })
      #(Model(..model, selected_studies: selected), effect.none())
    }

    DeleteSelected -> {
      case model.selected_studies {
        [] -> {
          #(
            model,
            context.dispatch_global(
              shared.ShowNotification("No studies selected", shared.Warning)
            )
          )
        }
        selected -> {
          // Проверяем права пользователя
          case context.user {
            Some(user) if user.is_superuser -> {
              #(model, delete_studies_effect(selected))
            }
            _ -> {
              #(
                model,
                context.dispatch_global(
                  shared.ShowError("You don't have permission to delete studies")
                )
              )
            }
          }
        }
      }
    }

    DeleteConfirmed(Ok(deleted_ids)) -> {
      let remaining = list.filter(model.studies, fn(s) {
        !list.contains(deleted_ids, s.study_uid)
      })

      let new_model = Model(
        ..model,
        studies: remaining,
        selected_studies: [],
        total: list.length(remaining)
      )

      // Инвалидируем кеш
      let cache_effect = context.dispatch_global(
        shared.InvalidateCachedData("studies_list_*")
      )

      let notification_effect = context.dispatch_global(
        shared.ShowSuccess(
          int.to_string(list.length(deleted_ids)) <> " studies deleted"
        )
      )

      #(new_model, effect.batch([cache_effect, notification_effect]))
    }

    NextPage -> {
      case model.page * model.per_page < model.total {
        True -> {
          let new_model = Model(..model, page: model.page + 1)
          update(new_model, LoadStudies, context)
        }
        False -> #(model, effect.none())
      }
    }

    PrevPage -> {
      case model.page > 1 {
        True -> {
          let new_model = Model(..model, page: model.page - 1)
          update(new_model, LoadStudies, context)
        }
        False -> #(model, effect.none())
      }
    }

    RefreshData -> {
      // Инвалидируем кеш и перезагружаем
      let invalidate_effect = context.dispatch_global(
        shared.InvalidateCachedData("studies_list_*")
      )

      #(model, effect.batch([invalidate_effect, load_studies_effect()]))
    }

    _ -> #(model, effect.none())
  }
}

// View функция
pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("studies-list-page")], [
    // Header
    view_header(model),

    // Filters and search
    view_filters(model),

    // Studies table or grid
    case model.loading {
      True -> view_loading()
      False -> {
        case model.error {
          Some(error) -> view_error(error)
          None -> view_studies_table(model)
        }
      }
    },

    // Pagination
    view_pagination(model)
  ])
}

fn view_header(model: Model) -> Element(Msg) {
  html.div([attribute.class("page-header")], [
    html.h2([], [html.text("Studies")]),
    html.div([attribute.class("header-actions")], [
      html.button(
        [
          attribute.class("btn btn-primary"),
          event.on_click(CreateNew)
        ],
        [html.text("New Study")]
      ),
      html.button(
        [
          attribute.class("btn btn-secondary"),
          event.on_click(RefreshData)
        ],
        [html.text("Refresh")]
      ),
      case model.selected_studies {
        [] -> element.none()
        _ -> {
          html.button(
            [
              attribute.class("btn btn-danger"),
              event.on_click(DeleteSelected)
            ],
            [html.text("Delete Selected")]
          )
        }
      }
    ])
  ])
}

fn view_filters(model: Model) -> Element(Msg) {
  html.div([attribute.class("filters-section")], [
    html.div([attribute.class("search-box")], [
      html.input([
        attribute.type_("text"),
        attribute.placeholder("Search studies..."),
        attribute.value(model.search_query),
        event.on_input(UpdateSearch)
      ]),
      html.button(
        [
          attribute.class("btn btn-search"),
          event.on_click(ApplyFilters)
        ],
        [html.text("Search")]
      )
    ]),

    html.div([attribute.class("sort-controls")], [
      html.select([event.on_change(fn(value) {
        case value {
          "date" -> SetSortField(ByDate)
          "patient" -> SetSortField(ByPatient)
          "modality" -> SetSortField(ByModality)
          "status" -> SetSortField(ByStatus)
          _ -> SetSortField(ByDate)
        }
      })], [
        html.option([attribute.value("date")], [html.text("Sort by Date")]),
        html.option([attribute.value("patient")], [html.text("Sort by Patient")]),
        html.option([attribute.value("modality")], [html.text("Sort by Modality")]),
        html.option([attribute.value("status")], [html.text("Sort by Status")])
      ]),

      html.button(
        [
          attribute.class("btn btn-icon"),
          event.on_click(ToggleSortOrder)
        ],
        [html.text(case model.sort_order {
          Ascending -> "↑"
          Descending -> "↓"
        })]
      )
    ])
  ])
}

fn view_studies_table(model: Model) -> Element(Msg) {
  html.div([attribute.class("studies-table")], [
    html.table([], [
      html.thead([], [
        html.tr([], [
          html.th([], [
            html.input([
              attribute.type_("checkbox"),
              attribute.checked(
                list.length(model.selected_studies) == list.length(model.studies)
              ),
              event.on_change(fn(checked) {
                case checked {
                  True -> SelectAll
                  False -> DeselectAll
                }
              })
            ])
          ]),
          html.th([], [html.text("Study UID")]),
          html.th([], [html.text("Patient")]),
          html.th([], [html.text("Date")]),
          html.th([], [html.text("Modality")]),
          html.th([], [html.text("Description")]),
          html.th([], [html.text("Actions")])
        ])
      ]),
      html.tbody([],
        list.map(model.studies, fn(study) {
          view_study_row(study, model.selected_studies)
        })
      )
    ])
  ])
}

fn view_study_row(study: Study, selected: List(String)) -> Element(Msg) {
  let is_selected = list.contains(selected, study.study_uid)

  html.tr([attribute.class(case is_selected {
    True -> "selected"
    False -> ""
  })], [
    html.td([], [
      html.input([
        attribute.type_("checkbox"),
        attribute.checked(is_selected),
        event.on_change(fn(checked) {
          case checked {
            True -> SelectStudy(study.study_uid)
            False -> DeselectStudy(study.study_uid)
          }
        })
      ])
    ]),
    html.td([], [html.text(study.study_uid)]),
    html.td([], [html.text(study.patient_id)]),
    html.td([], [html.text(study.date)]),
    html.td([], [html.text(option.unwrap(study.modalities_in_study, ""))]),
    html.td([], [html.text(option.unwrap(study.study_description, ""))]),
    html.td([], [
      html.button(
        [
          attribute.class("btn btn-sm btn-info"),
          event.on_click(ViewDetails(study.study_uid))
        ],
        [html.text("View")]
      )
    ])
  ])
}

fn view_pagination(model: Model) -> Element(Msg) {
  let total_pages = (model.total + model.per_page - 1) / model.per_page

  html.div([attribute.class("pagination")], [
    html.button(
      [
        attribute.class("btn btn-pagination"),
        attribute.disabled(model.page <= 1),
        event.on_click(PrevPage)
      ],
      [html.text("Previous")]
    ),

    html.span([attribute.class("page-info")], [
      html.text(
        "Page " <> int.to_string(model.page) <>
        " of " <> int.to_string(total_pages)
      )
    ]),

    html.button(
      [
        attribute.class("btn btn-pagination"),
        attribute.disabled(model.page >= total_pages),
        event.on_click(NextPage)
      ],
      [html.text("Next")]
    )
  ])
}

fn view_loading() -> Element(Msg) {
  html.div([attribute.class("loading")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading studies...")])
  ])
}

fn view_error(error: String) -> Element(Msg) {
  html.div([attribute.class("error-message")], [
    html.p([], [html.text("Error: " <> error)]),
    html.button(
      [
        attribute.class("btn btn-primary"),
        event.on_click(LoadStudies)
      ],
      [html.text("Retry")]
    )
  ])
}

// Эффекты
fn load_studies_effect() -> Effect(Msg) {
  effect.from(fn(dispatch) {
    api.get_studies()
    |> promise.tap(fn(result) {
      dispatch(StudiesLoaded(result))
    })
    Nil
  })
}

fn delete_studies_effect(study_uids: List(String)) -> Effect(Msg) {
  effect.from(fn(dispatch) {
    api.delete_studies(study_uids)
    |> promise.tap(fn(result) {
      dispatch(DeleteConfirmed(result))
    })
    Nil
  })
}

// Вспомогательные функции
fn filter_studies(studies: List(Study), query: String) -> List(Study) {
  case query {
    "" -> studies
    _ -> {
      let lower_query = string.lowercase(query)
      list.filter(studies, fn(study) {
        string.contains(string.lowercase(study.study_uid), lower_query) ||
        string.contains(string.lowercase(study.patient_id), lower_query) ||
        option.map(study.study_description, fn(desc) {
          string.contains(string.lowercase(desc), lower_query)
        }) |> option.unwrap(False)
      })
    }
  }
}

fn decode_studies(data: Dynamic) -> Result(List(Study), String) {
  // Декодирование из Dynamic в List(Study)
  dynamic.list(data, models.study_decoder())
  |> result.map_error(fn(_) { "Failed to decode cached studies" })
}
```

### 4. Главное приложение с композицией (main.gleam)

```gleam
// main.gleam
import gleam/dict
import gleam/option.{type Option, None, Some}
import lustre
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import modem
import router.{type Route}

// Core modules
import core/store
import core/messages.{type Msg, Global, Page}
import core/shared.{type SharedContext}

// Page modules
import pages/home
import pages/auth/login
import pages/auth/register
import pages/studies/list as studies_list
import pages/studies/create as studies_create
import pages/patients/list as patients_list
import pages/tasks/execute as tasks_execute
import pages/admin/settings as admin_settings

// Components
import components/layout

pub fn main() {
  let app = lustre.application(init, update, view)
  let assert Ok(_) = lustre.start(app, "#app", Nil)
  Nil
}

fn init(_) -> #(store.Model, Effect(Msg)) {
  let model = store.init()

  let initial_route = case modem.initial_uri() {
    Ok(uri) -> router.parse_route(uri)
    Error(_) -> router.Home
  }

  #(model, modem.init(on_url_change))
}

fn on_url_change(uri) -> Msg {
  Global(messages.RouteChanged(router.parse_route(uri)))
}

fn update(model: store.Model, msg: Msg) -> #(store.Model, Effect(Msg)) {
  case msg {
    Global(global_msg) -> handle_global_message(model, global_msg)
    Page(page_msg) -> handle_page_message(model, page_msg)
  }
}

fn handle_global_message(
  model: store.Model,
  msg: messages.GlobalMsg
) -> #(store.Model, Effect(Msg)) {
  case msg {
    messages.RouteChanged(route) -> {
      // Инициализация страницы при смене роута
      let #(new_model, page_effect) = init_page_for_route(model, route)
      #(store.set_route(new_model, route), page_effect)
    }

    messages.UserLoggedIn(user) -> {
      let new_model = store.set_user(model, user)

      // Уведомляем текущую страницу об изменении пользователя
      let page_effect = notify_current_page_user_changed(new_model, Some(user))

      #(new_model, effect.batch([
        page_effect,
        effect.from(fn(dispatch) {
          dispatch(Global(messages.NavigateTo(router.Home)))
          Nil
        })
      ]))
    }

    messages.UserLoggedOut -> {
      let new_model = store.clear_user(model) |> store.clear_cache()

      #(new_model, effect.from(fn(dispatch) {
        dispatch(Global(messages.NavigateTo(router.Login)))
        Nil
      }))
    }

    messages.ShowNotification(text, notification_type) -> {
      #(store.show_notification(model, text, notification_type), effect.none())
    }

    messages.CacheSet(key, value) -> {
      #(store.set_cache(model, key, value), effect.none())
    }

    _ -> #(model, effect.none())
  }
}

fn handle_page_message(
  model: store.Model,
  msg: messages.PageMsg
) -> #(store.Model, Effect(Msg)) {
  // Создаем контекст для страницы
  let context = shared.SharedContext(
    user: model.user,
    cache: model.cache,
    dispatch_global: fn(request) {
      handle_global_request(request)
    }
  )

  case msg {
    messages.StudiesListMsg(page_msg) -> {
      let #(new_page_model, page_effect) =
        studies_list.update(model.studies_list_page, page_msg, context)

      #(
        store.set_page_model(model, StudiesListPage(new_page_model)),
        effect.map(page_effect, fn(m) { Page(messages.StudiesListMsg(m)) })
      )
    }

    messages.LoginMsg(page_msg) -> {
      let #(new_page_model, page_effect) =
        login.update(model.login_page, page_msg)

      // Специальная обработка для логина
      let composed_effect = case page_msg {
        login.LoginSuccess(user) -> {
          effect.batch([
            effect.map(page_effect, fn(m) { Page(messages.LoginMsg(m)) }),
            effect.from(fn(dispatch) {
              dispatch(Global(messages.UserLoggedIn(user)))
              Nil
            })
          ])
        }
        _ -> effect.map(page_effect, fn(m) { Page(messages.LoginMsg(m)) })
      }

      #(
        store.set_page_model(model, LoginPage(new_page_model)),
        composed_effect
      )
    }

    // Аналогично для других страниц...
    _ -> #(model, effect.none())
  }
}

fn view(model: store.Model) -> Element(Msg) {
  let page_content = view_current_page(model)

  case model.route {
    router.Login | router.Register -> page_content
    _ -> layout.view(model, page_content)
  }
}

fn view_current_page(model: store.Model) -> Element(Msg) {
  case model.route {
    router.Home ->
      home.view(model.home_page)
      |> element.map(fn(m) { Page(messages.HomeMsg(m)) })

    router.Login ->
      login.view(model.login_page)
      |> element.map(fn(m) { Page(messages.LoginMsg(m)) })

    router.Studies ->
      studies_list.view(model.studies_list_page)
      |> element.map(fn(m) { Page(messages.StudiesListMsg(m)) })

    router.StudiesCreate ->
      studies_create.view(model.studies_create_page)
      |> element.map(fn(m) { Page(messages.StudiesCreateMsg(m)) })

    // И так далее для всех страниц...
    _ -> html.div([], [html.text("404 - Page not found")])
  }
}

fn init_page_for_route(
  model: store.Model,
  route: Route
) -> #(store.Model, Effect(Msg)) {
  case route {
    router.Studies -> {
      let #(page_model, page_effect) = studies_list.init()
      #(
        store.set_page_model(model, StudiesListPage(page_model)),
        effect.map(page_effect, fn(m) { Page(messages.StudiesListMsg(m)) })
      )
    }

    router.StudiesCreate -> {
      let #(page_model, page_effect) = studies_create.init()
      #(
        store.set_page_model(model, StudiesCreatePage(page_model)),
        effect.map(page_effect, fn(m) { Page(messages.StudiesCreateMsg(m)) })
      )
    }

    // Для страниц, требующих параметры
    router.TaskExecute(task_id) -> {
      let #(page_model, page_effect) = tasks_execute.init(task_id)
      #(
        store.set_page_model(model, TaskExecutePage(page_model)),
        effect.map(page_effect, fn(m) { Page(messages.TasksExecuteMsg(m)) })
      )
    }

    _ -> #(model, effect.none())
  }
}

fn handle_global_request(request: shared.GlobalRequest) -> Effect(Msg) {
  case request {
    shared.NavigateToRoute(route) ->
      effect.from(fn(dispatch) {
        dispatch(Global(messages.NavigateTo(route)))
        Nil
      })

    shared.ShowNotification(text, notification_type) ->
      effect.from(fn(dispatch) {
        dispatch(Global(messages.ShowNotification(text, notification_type)))
        Nil
      })

    shared.SetCachedData(key, value) ->
      effect.from(fn(dispatch) {
        dispatch(Global(messages.CacheSet(key, value)))
        Nil
      })

    shared.InvalidateCachedData(key) ->
      effect.from(fn(dispatch) {
        dispatch(Global(messages.CacheInvalidate(key)))
        Nil
      })

    _ -> effect.none()
  }
}
```

### 5. Обновленное глобальное состояние (core/store.gleam)

```gleam
// core/store.gleam
import gleam/dict.{type Dict}
import gleam/option.{type Option, None}
import router.{type Route}
import api/models.{type User}
import core/shared.{type NotificationType}

// Импорт моделей страниц
import pages/home
import pages/auth/login
import pages/auth/register
import pages/studies/list as studies_list
import pages/studies/create as studies_create
import pages/patients/list as patients_list
import pages/tasks/execute as tasks_execute
import pages/admin/settings as admin_settings

// Глобальное состояние
pub type Model {
  Model(
    // Навигация
    route: Route,

    // Глобальные данные
    user: Option(User),
    cache: Dict(String, Dynamic),

    // Уведомления
    notifications: List(Notification),

    // Модели страниц
    home_page: home.Model,
    login_page: login.Model,
    register_page: register.Model,
    studies_list_page: studies_list.Model,
    studies_create_page: studies_create.Model,
    patients_list_page: patients_list.Model,
    tasks_execute_page: tasks_execute.Model,
    admin_settings_page: admin_settings.Model,

    // Системное
    loading: Bool,
    error: Option(String)
  )
}

pub type Notification {
  Notification(
    id: String,
    text: String,
    notification_type: NotificationType,
    timestamp: Int
  )
}

// Типобезопасные обертки для моделей страниц
pub type PageModel {
  HomePage(home.Model)
  LoginPage(login.Model)
  RegisterPage(register.Model)
  StudiesListPage(studies_list.Model)
  StudiesCreatePage(studies_create.Model)
  PatientsListPage(patients_list.Model)
  TaskExecutePage(tasks_execute.Model)
  AdminSettingsPage(admin_settings.Model)
}

pub fn init() -> Model {
  Model(
    route: router.Home,
    user: None,
    cache: dict.new(),
    notifications: [],

    // Инициализация с дефолтными значениями
    home_page: home.init_model(),
    login_page: login.init_model(),
    register_page: register.init_model(),
    studies_list_page: studies_list.init_model(),
    studies_create_page: studies_create.init_model(),
    patients_list_page: patients_list.init_model(),
    tasks_execute_page: tasks_execute.init_model(),
    admin_settings_page: admin_settings.init_model(),

    loading: False,
    error: None
  )
}

// Helper функции для обновления состояния
pub fn set_route(model: Model, route: Route) -> Model {
  Model(..model, route: route)
}

pub fn set_user(model: Model, user: User) -> Model {
  Model(..model, user: Some(user))
}

pub fn clear_user(model: Model) -> Model {
  Model(..model, user: None)
}

pub fn set_cache(model: Model, key: String, value: Dynamic) -> Model {
  Model(..model, cache: dict.insert(model.cache, key, value))
}

pub fn clear_cache(model: Model) -> Model {
  Model(..model, cache: dict.new())
}

pub fn set_page_model(model: Model, page: PageModel) -> Model {
  case page {
    HomePage(m) -> Model(..model, home_page: m)
    LoginPage(m) -> Model(..model, login_page: m)
    RegisterPage(m) -> Model(..model, register_page: m)
    StudiesListPage(m) -> Model(..model, studies_list_page: m)
    StudiesCreatePage(m) -> Model(..model, studies_create_page: m)
    PatientsListPage(m) -> Model(..model, patients_list_page: m)
    TaskExecutePage(m) -> Model(..model, tasks_execute_page: m)
    AdminSettingsPage(m) -> Model(..model, admin_settings_page: m)
  }
}

pub fn show_notification(
  model: Model,
  text: String,
  notification_type: NotificationType
) -> Model {
  let notification = Notification(
    id: generate_id(),
    text: text,
    notification_type: notification_type,
    timestamp: current_timestamp()
  )

  Model(..model, notifications: [notification, ..model.notifications])
}
```

## План миграции

### Фаза 1: Подготовка (1-2 дня)
1. ✅ Анализ текущей архитектуры
2. ✅ Создание плана рефакторинга
3. Создание новой структуры папок
4. Настройка зависимостей в gleam.toml

### Фаза 2: Создание ядра (2-3 дня)
1. Реализация `core/messages.gleam`
2. Реализация `core/shared.gleam`
3. Реализация `core/store.gleam`
4. Создание helper функций для композиции эффектов

### Фаза 3: Миграция первой страницы (2 дня)
1. Выбор простой страницы (например, home)
2. Создание модульной версии
3. Интеграция в главное приложение
4. Тестирование работы

### Фаза 4: Миграция критических страниц (5-7 дней)
1. Миграция login/register
2. Миграция studies/list
3. Миграция tasks/execute
4. Проверка взаимодействия между страницами

### Фаза 5: Миграция остальных страниц (3-5 дней)
1. Patients pages
2. Admin pages
3. Остальные task pages
4. Study detail pages

### Фаза 6: Очистка и оптимизация (2 дня)
1. Удаление старого кода
2. Оптимизация импортов
3. Рефакторинг дублированного кода
4. Создание переиспользуемых компонентов

### Фаза 7: Тестирование и документация (2 дня)
1. Комплексное тестирование
2. Обновление документации
3. Создание примеров использования
4. Финальная проверка

## Потенциальные проблемы и решения

### Проблема: Дублирование кода между страницами
**Решение**: Создание общих компонентов и утилит в `components/` и `utils/`

### Проблема: Сложность коммуникации между страницами
**Решение**: Использование глобального кеша и SharedContext для обмена данными

### Проблема: Большой размер главного файла
**Решение**: Разделение на несколько файлов (router.gleam, effects.gleam, etc.)

### Проблема: Управление зависимостями страниц
**Решение**: Явное описание зависимостей через SharedContext

## Заключение

Предложенная модульная архитектура решит текущие проблемы масштабирования и сделает приложение более поддерживаемым. Каждая страница становится независимым модулем, что упрощает разработку, тестирование и поддержку.

Ключевые принципы:
- Модульность и изоляция
- Композиция через element.map
- Общий контекст для коммуникации
- Типобезопасность на всех уровнях

Эта архитектура позволит легко добавлять новые страницы (исследования, пациенты, админ-панель, задачи) без увеличения сложности основного кода.