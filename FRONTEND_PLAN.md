# План интеграции Gleam+Lustre фронтенда в Clarinet

## Обзор

Интеграция SPA фронтенда на Gleam+Lustre в существующий FastAPI backend. Фронтенд будет опциональным модулем, обслуживаемым через основной FastAPI сервер, с поддержкой пользовательской кастомизации стилей.

## 1. Архитектура

### 1.1 Структура директорий

```scheme
clarinet/
├── src/
│   ├── api/                     # Существующий backend
│   ├── cli/                     # CLI интерфейс
│   ├── frontend/                # Новый фронтенд модуль
│   │   ├── __init__.py
│   │   ├── gleam.toml          # Конфигурация Gleam
│   │   ├── manifest.toml       # Зависимости
│   │   ├── src/                # Исходный код Gleam
│   │   │   ├── clarinet.gleam  # Точка входа
│   │   │   ├── main.gleam      # Инициализация Lustre
│   │   │   ├── api/            # API клиент
│   │   │   │   ├── client.gleam
│   │   │   │   ├── auth.gleam
│   │   │   │   ├── study.gleam
│   │   │   │   ├── task.gleam
│   │   │   │   ├── user.gleam
│   │   │   │   ├── types.gleam
│   │   │   │   └── models.gleam        # Статические типы моделей
│   │   │   ├── components/     # Переиспользуемые компоненты
│   │   │   │   ├── layout.gleam
│   │   │   │   ├── navbar.gleam
│   │   │   │   ├── forms/          # Статические типизированные формы
│   │   │   │   │   ├── base.gleam  # Базовые элементы форм
│   │   │   │   │   ├── patient_form.gleam
│   │   │   │   │   ├── study_form.gleam
│   │   │   │   │   ├── task_design_form.gleam
│   │   │   │   │   └── user_form.gleam
│   │   │   │   ├── formosh_wrapper.gleam  # Обертка для Task.result
│   │   │   │   ├── tables.gleam
│   │   │   │   └── modals.gleam
│   │   │   ├── pages/          # Страницы приложения
│   │   │   │   ├── home.gleam
│   │   │   │   ├── login.gleam
│   │   │   │   ├── studies/
│   │   │   │   │   ├── list.gleam
│   │   │   │   │   └── detail.gleam
│   │   │   │   ├── tasks/
│   │   │   │   │   ├── list.gleam
│   │   │   │   │   ├── detail.gleam
│   │   │   │   │   ├── design.gleam    # Статическая форма для TaskDesign
│   │   │   │   │   └── execute.gleam   # Динамическая форма для Task.result
│   │   │   │   └── users/
│   │   │   │       ├── list.gleam
│   │   │   │       └── profile.gleam
│   │   │   ├── router.gleam    # Клиентский роутинг
│   │   │   ├── store.gleam     # Глобальное состояние
│   │   │   └── utils.gleam     # Вспомогательные функции
│   │   ├── build/              # Скомпилированные файлы
│   │   │   └── dev/
│   │   │       └── javascript/
│   │   │           ├── clarinet.mjs
│   │   │           └── preload.mjs
│   │   ├── static/             # Статические ресурсы
│   │   │   ├── index.html
│   │   │   ├── base.css       # Базовые стили фреймворка
│   │   │   └── favicon.ico
│   │   └── scripts/            # Build скрипты
│   │       ├── build.sh
│   │       └── watch.sh
│   └── settings.py             # Настройки с поддержкой фронтенда

# Пользовательский проект (создается через clarinet init)
user_project/
├── settings.toml               # Настройки проекта
├── static/                     # Кастомизация UI
│   ├── custom.css             # Пользовательские стили
│   ├── logo.png               # Логотип
│   └── theme/                 # Тема оформления
│       ├── colors.css
│       └── fonts.css
└── tasks/                      # Определения задач
```

### 1.2 Компоненты системы

1. **Gleam/Lustre фронтенд**: SPA приложение, компилируемое в JavaScript
2. **FastAPI backend**: Существующий API, расширенный для обслуживания статики
3. **CLI интерфейс**: Команды для управления фронтендом
4. **Статические ресурсы**: Базовые и пользовательские стили

## 2. Детальная реализация

### 2.1 Настройка Gleam проекта

**gleam.toml:**

```toml
name = "clarinet_frontend"
version = "1.0.0"
target = "javascript"
description = "Clarinet medical imaging framework frontend"

[dependencies]
lustre = "~> 4.2"
lustre_http = "~> 1.2"
lustre_ui = "~> 0.6"
gleam_json = "~> 2.0"
gleam_javascript = "~> 0.8"
gleam_stdlib = "~> 0.38"
modem = "~> 1.3"
formosh = "~> 0.1"  # Для динамических форм Task.result
gleam_fetch = "~> 0.4"  # Для загрузки схем

[dev-dependencies]
gleeunit = "~> 1.0"

[javascript]
entry_point = "clarinet.gleam"
output = "build/dev/javascript/clarinet.mjs"
```

### 2.2 API клиент

**src/frontend/src/api/client.gleam:**

```gleam
import gleam/http/request
import gleam/http/response
import gleam/json
import gleam/result
import gleam/option.{Option, Some, None}
import lustre_http

pub type ApiConfig {
  ApiConfig(
    base_url: String,
    token: Option(String),
  )
}

pub type ApiError {
  NetworkError(String)
  ParseError(String)
  AuthError(String)
  ServerError(Int, String)
}

pub fn create_client(base_url: String) -> ApiConfig {
  ApiConfig(base_url: base_url, token: None)
}

pub fn with_token(config: ApiConfig, token: String) -> ApiConfig {
  ApiConfig(..config, token: Some(token))
}

pub fn get(config: ApiConfig, path: String, decoder: json.Decoder(a)) {
  let url = config.base_url <> "/api" <> path
  
  lustre_http.get(url, decoder)
  |> add_auth_header(config.token)
}

pub fn post(config: ApiConfig, path: String, body: json.Json, decoder: json.Decoder(a)) {
  let url = config.base_url <> "/api" <> path
  
  lustre_http.post(url, body, decoder)
  |> add_auth_header(config.token)
}

fn add_auth_header(request, token: Option(String)) {
  case token {
    Some(t) -> request |> lustre_http.header("Authorization", "Bearer " <> t)
    None -> request
  }
}
```

### 2.3 Основное приложение

**src/frontend/src/main.gleam:**

```gleam
import lustre
import lustre/element.{Element}
import lustre/element/html
import lustre/event
import lustre/effect.{Effect}
import modem
import router.{Route}
import store.{Model, Msg}
import pages/login
import pages/home
import components/layout

pub fn main() {
  let app = lustre.application(init, update, view)
  let assert Ok(_) = lustre.start(app, "#app", Nil)
  Nil
}

fn init(_) -> #(Model, Effect(Msg)) {
  let model = store.init()
  let router = modem.init(router.on_route_change)
  #(model, router)
}

fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg)) {
  case msg {
    store.OnRouteChange(route) -> {
      let new_model = store.set_route(model, route)
      #(new_model, effect.none())
    }
    store.Navigate(route) -> {
      let new_model = store.set_route(model, route)
      #(new_model, modem.push(router.route_to_path(route)))
    }
    store.LoginSuccess(token, user) -> {
      let new_model = model
        |> store.set_auth(token, user)
        |> store.set_route(router.Home)
      #(new_model, modem.push("/"))
    }
    // ... другие сообщения
  }
}

fn view(model: Model) -> Element(Msg) {
  layout.view(model, page_content(model))
}

fn page_content(model: Model) -> Element(Msg) {
  case model.route {
    router.Login -> login.view(model)
    router.Home -> home.view(model)
    router.Studies -> studies.list.view(model)
    router.StudyDetail(id) -> studies.detail.view(model, id)
    // ... другие страницы
  }
}
```

### 2.4 Модуль роутинга с Modem

**src/frontend/src/router.gleam:**

```gleam
import gleam/uri
import gleam/option.{Option, Some, None}
import gleam/string
import gleam/list
import gleam/result
import modem
import store.{Msg}

// Определение маршрутов
pub type Route {
  Home
  Login
  Studies
  StudyDetail(id: String)
  Tasks
  TaskDetail(id: String)
  TaskNew
  Users
  UserProfile(id: String)
  NotFound
}

// Преобразование Route в путь URL
pub fn route_to_path(route: Route) -> String {
  case route {
    Home -> "/"
    Login -> "/login"
    Studies -> "/studies"
    StudyDetail(id) -> "/studies/" <> id
    Tasks -> "/tasks"
    TaskDetail(id) -> "/tasks/" <> id
    TaskNew -> "/tasks/new"
    Users -> "/users"
    UserProfile(id) -> "/users/" <> id
    NotFound -> "/404"
  }
}

// Парсинг URL в Route
pub fn parse_route(uri: uri.Uri) -> Route {
  let path = uri.path
    |> string.split("/")
    |> list.filter(fn(s) { string.length(s) > 0 })
  
  case path {
    [] -> Home
    ["login"] -> Login
    ["studies"] -> Studies
    ["studies", id] -> StudyDetail(id)
    ["tasks"] -> Tasks
    ["tasks", "new"] -> TaskNew
    ["tasks", id] -> TaskDetail(id)
    ["users"] -> Users
    ["users", id] -> UserProfile(id)
    _ -> NotFound
  }
}

// Обработчик изменения маршрута для Modem
pub fn on_route_change(uri: uri.Uri) -> Msg {
  let route = parse_route(uri)
  store.OnRouteChange(route)
}

// Навигация к маршруту
pub fn navigate_to(route: Route) -> modem.Effect(Msg) {
  modem.push(route_to_path(route))
}

// Замена текущего маршрута
pub fn replace_with(route: Route) -> modem.Effect(Msg) {
  modem.replace(route_to_path(route))
}

// Проверка доступа к маршруту
pub fn requires_auth(route: Route) -> Bool {
  case route {
    Login -> False
    _ -> True
  }
}

// Получение параметров из маршрута
pub fn get_route_params(route: Route) -> Option(String) {
  case route {
    StudyDetail(id) -> Some(id)
    TaskDetail(id) -> Some(id)
    UserProfile(id) -> Some(id)
    _ -> None
  }
}
```

### 2.5 Гибридный подход к формам

Проект использует два подхода для работы с формами в зависимости от типа данных:

#### 2.5.1 Статические типизированные формы (Patient, Study, TaskDesign, User)

Для основных моделей данных используются полностью типизированные формы на чистом Gleam/Lustre:

**src/frontend/src/api/models.gleam:**

```gleam
// Статические типы для основных моделей
pub type Patient {
  Patient(
    id: Option(Int),
    name: String,
    birth_date: String,
    medical_record: String,
    gender: Gender,
    notes: Option(String)
  )
}

pub type Study {
  Study(
    id: Option(Int),
    patient_id: Int,
    modality: String,
    description: String,
    study_date: String,
    institution: String,
    series_count: Int
  )
}

pub type TaskDesign {
  TaskDesign(
    id: Option(Int),
    name: String,
    description: String,
    category: String,
    result_schema: Json,  // JSON Schema для динамической формы
    is_active: Bool
  )
}

pub type User {
  User(
    id: Int,
    username: String,
    email: String,
    role: UserRole,
    is_active: Bool
  )
}
```

**Пример статической формы - src/frontend/src/components/forms/study_form.gleam:**

```gleam
import lustre/element.{Element}
import lustre/element/html
import lustre/event
import lustre/attribute
import lustre_ui/input
import lustre_ui/button
import api/models.{Study}
import store.{Msg}

pub type StudyFormData {
  StudyFormData(
    patient_id: String,
    modality: String,
    description: String,
    study_date: String,
    institution: String
  )
}

pub fn view(form_data: StudyFormData, errors: Dict(String, String)) -> Element(Msg) {
  html.form([
    attribute.class("study-form"),
    event.on_submit(fn(e) { 
      event.prevent_default(e)
      store.SubmitStudyForm(form_data)
    })
  ], [
    // Patient ID field
    html.div([attribute.class("form-group")], [
      html.label([attribute.for("patient_id")], [html.text("Patient ID")]),
      input.text([
        attribute.id("patient_id"),
        attribute.value(form_data.patient_id),
        attribute.required(True),
        event.on_input(fn(value) { 
          store.UpdateStudyForm(StudyFormData(..form_data, patient_id: value))
        })
      ]),
      error_message(errors, "patient_id")
    ]),
    
    // Modality dropdown
    html.div([attribute.class("form-group")], [
      html.label([attribute.for("modality")], [html.text("Modality")]),
      html.select([
        attribute.id("modality"),
        event.on_change(fn(value) {
          store.UpdateStudyForm(StudyFormData(..form_data, modality: value))
        })
      ], [
        html.option([attribute.value("CT")], [html.text("CT")]),
        html.option([attribute.value("MR")], [html.text("MRI")]),
        html.option([attribute.value("US")], [html.text("Ultrasound")]),
        html.option([attribute.value("XR")], [html.text("X-Ray")])
      ])
    ]),
    
    // Other fields...
    
    // Submit button
    button.primary([
      attribute.type_("submit")
    ], [html.text("Create Study")])
  ])
}

fn error_message(errors: Dict(String, String), field: String) -> Element(Msg) {
  case dict.get(errors, field) {
    Ok(error) -> html.span([attribute.class("error")], [html.text(error)])
    Error(_) -> html.text("")
  }
}
```

**Преимущества статических форм:**

- Полная типобезопасность на этапе компиляции
- Автокомплит и подсказки в IDE
- Прямое соответствие моделям backend
- Оптимальная производительность
- Простота отладки

#### 2.5.2 Динамические формы через Formosh (Task.result)

Для результатов задач используется Formosh - генератор форм на основе JSON Schema:

**src/frontend/src/components/formosh_wrapper.gleam:**

```gleam
import lustre/element.{Element}
import lustre/element/html
import lustre/attribute
import lustre/effect.{Effect}
import gleam/json.{Json}
import formosh
import store.{Msg}

pub fn render_task_form(
  schema: Json,
  initial_data: Option(Json),
  on_submit: fn(Json) -> Msg
) -> Element(Msg) {
  html.div([
    attribute.class("formosh-container"),
    attribute.id("task-result-form")
  ], [
    // Formosh web component
    html.node("formosh-form", [
      attribute.property("schema", schema),
      attribute.property("value", initial_data |> option.unwrap(json.object([]))),
      attribute.property("locale", "en"),
      attribute.on("submit", fn(event) {
        let data = event.detail
        on_submit(data)
      })
    ], [])
  ])
}
```

**src/frontend/src/pages/tasks/execute.gleam:**

```gleam
import lustre/element.{Element}
import lustre/element/html
import lustre/effect.{Effect}
import gleam/json
import gleam/result
import api/client
import api/types.{Task, TaskDesign}
import components/formosh_wrapper
import store.{Model, Msg}

pub fn view(model: Model, task_id: String) -> Element(Msg) {
  case dict.get(model.tasks, task_id) {
    Ok(task) -> render_task_execution(model, task)
    Error(_) -> loading_view()
  }
}

fn render_task_execution(model: Model, task: Task) -> Element(Msg) {
  html.div([attribute.class("task-execution")], [
    html.h2([], [html.text(task.design.name)]),
    html.p([], [html.text(task.design.description)]),
    
    // Динамическая форма на основе result_schema
    formosh_wrapper.render_task_form(
      schema: task.design.result_schema,
      initial_data: task.result,
      on_submit: fn(data) { store.SubmitTaskResult(task.id, data) }
    )
  ])
}

// Обработка отправки в update функции
pub fn handle_submit_task_result(
  model: Model,
  task_id: String,
  result: Json
) -> #(Model, Effect(Msg)) {
  let submit_effect = 
    client.post(
      model.api_config,
      "/tasks/" <> task_id <> "/result",
      result,
      task_result_decoder
    )
    |> effect.map(fn(response) {
      case response {
        Ok(updated_task) -> store.TaskResultSaved(updated_task)
        Error(error) -> store.ShowError("Failed to save task result")
      }
    })
  
  #(Model(..model, loading: True), submit_effect)
}
```

**Преимущества динамических форм Formosh:**

- Автоматическая генерация UI из JSON Schema
- Встроенная валидация на основе constraints
- Поддержка сложных структур (nested objects, arrays)
- Условная логика (if/then/else в схеме)
- Не требует изменения кода при добавлении новых типов задач

#### 2.5.3 Пример статической формы для TaskDesign

**src/frontend/src/pages/tasks/design.gleam:**

```gleam
import lustre/element.{Element}
import lustre/element/html
import lustre/event
import lustre/attribute
import gleam/json
import api/models.{TaskDesign}
import components/forms/task_design_form
import store.{Model, Msg}

pub fn view(model: Model, task_design_id: Option(String)) -> Element(Msg) {
  let form_data = case task_design_id {
    Some(id) -> load_existing_design(model, id)
    None -> empty_design_form()
  }
  
  html.div([attribute.class("task-design-page")], [
    html.h2([], [
      html.text(case task_design_id {
        Some(_) -> "Edit Task Design"
        None -> "Create New Task Design"
      })
    ]),
    
    // Статическая форма для основных полей TaskDesign
    task_design_form.view(form_data, model.form_errors),
    
    // JSON Schema редактор для result_schema
    html.div([attribute.class("schema-editor-section")], [
      html.h3([], [html.text("Result Schema (JSON Schema)")]),
      html.textarea([
        attribute.class("json-editor"),
        attribute.rows(20),
        attribute.value(json.to_string(form_data.result_schema)),
        event.on_input(fn(value) {
          case json.parse(value) {
            Ok(schema) -> store.UpdateTaskDesignSchema(schema)
            Error(_) -> store.ShowSchemaError("Invalid JSON")
          }
        })
      ], []),
      
      // Предпросмотр формы
      html.div([attribute.class("form-preview")], [
        html.h4([], [html.text("Form Preview")]),
        formosh_wrapper.render_task_form(
          schema: form_data.result_schema,
          initial_data: None,
          on_submit: fn(_) { store.NoOp }  // Только предпросмотр
        )
      ])
    ])
  ])
}
```

#### 2.5.4 Примеры JSON Schema для Formosh

**Пример схемы для радиологического отчета:**

```json
{
  "type": "object",
  "title": "Radiology Report",
  "required": ["findings", "conclusion"],
  "properties": {
    "findings": {
      "type": "string",
      "title": "Findings",
      "description": "Describe the observed findings",
      "minLength": 10,
      "ui:widget": "textarea"
    },
    "measurements": {
      "type": "array",
      "title": "Measurements",
      "items": {
        "type": "object",
        "properties": {
          "location": {
            "type": "string",
            "title": "Location"
          },
          "value": {
            "type": "number",
            "title": "Value (mm)"
          }
        }
      }
    },
    "conclusion": {
      "type": "string",
      "title": "Conclusion",
      "enum": ["normal", "abnormal", "follow-up required"],
      "ui:widget": "radio"
    }
  }
}
```

### 2.6 Интеграция с FastAPI

**Изменения в src/api/app.py:**

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from src.settings import settings

app = FastAPI(title="Clarinet API", version="1.0.0")

# API роуты монтируются с префиксом /api
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(study_router, prefix="/api/studies", tags=["studies"])
app.include_router(task_router, prefix="/api/tasks", tags=["tasks"])
app.include_router(user_router, prefix="/api/users", tags=["users"])
app.include_router(slicer_router, prefix="/api/slicer", tags=["slicer"])

# Обслуживание фронтенда
if settings.frontend_enabled:
    frontend_build = Path("src/frontend/build/dev/javascript")
    frontend_static = Path("src/frontend/static")
    
    # Пользовательские статические файлы (высший приоритет)
    if settings.project_path and (settings.project_path / "static").exists():
        app.mount(
            "/static/custom",
            StaticFiles(directory=settings.project_path / "static"),
            name="custom_static"
        )
    
    # Базовые статические файлы фронтенда
    if frontend_static.exists():
        app.mount(
            "/static",
            StaticFiles(directory=frontend_static),
            name="frontend_static"
        )
    
    # Скомпилированный JavaScript
    if frontend_build.exists():
        app.mount(
            "/js",
            StaticFiles(directory=frontend_build),
            name="frontend_js"
        )
    
    # SPA fallback - все неизвестные пути возвращают index.html
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Проверяем, не является ли это API запросом
        if full_path.startswith("api/"):
            return {"error": "Not found"}, 404
        
        index_file = frontend_static / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"error": "Frontend not built"}, 404
```

### 2.6 CLI команды

**Расширение src/cli/main.py:**

```python
def add_frontend_commands(subparsers):
    """Добавление команд для управления фронтендом."""
    frontend_parser = subparsers.add_parser("frontend", help="Frontend management")
    frontend_subparsers = frontend_parser.add_subparsers(dest="frontend_command")
    
    # frontend install
    frontend_subparsers.add_parser(
        "install",
        help="Install Gleam and frontend dependencies"
    )
    
    # frontend build
    build_parser = frontend_subparsers.add_parser(
        "build",
        help="Build frontend for production"
    )
    build_parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch for changes and rebuild"
    )
    
    # frontend clean
    frontend_subparsers.add_parser(
        "clean",
        help="Clean build artifacts"
    )

def handle_frontend_command(args):
    """Обработка команд фронтенда."""
    if args.frontend_command == "install":
        install_frontend()
    elif args.frontend_command == "build":
        build_frontend(watch=args.watch)
    elif args.frontend_command == "clean":
        clean_frontend()

def install_frontend():
    """Установка Gleam и зависимостей."""
    import subprocess
    
    # Проверяем наличие Gleam
    try:
        subprocess.run(["gleam", "--version"], check=True, capture_output=True)
        logger.info("Gleam already installed")
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.info("Installing Gleam...")
        subprocess.run([
            "sh", "-c",
            "curl -fsSL https://gleam.run/install.sh | sh"
        ], check=True)
    
    # Устанавливаем зависимости
    frontend_path = Path("src/frontend")
    if frontend_path.exists():
        logger.info("Installing frontend dependencies...")
        subprocess.run(
            ["gleam", "deps", "download"],
            cwd=frontend_path,
            check=True
        )
        logger.info("Frontend dependencies installed")

def build_frontend(watch=False):
    """Сборка фронтенда."""
    import subprocess
    
    frontend_path = Path("src/frontend")
    if not frontend_path.exists():
        logger.error("Frontend directory not found")
        return
    
    if watch:
        logger.info("Starting frontend build in watch mode...")
        subprocess.run(
            ["gleam", "build", "--watch"],
            cwd=frontend_path
        )
    else:
        logger.info("Building frontend...")
        subprocess.run(
            ["gleam", "build"],
            cwd=frontend_path,
            check=True
        )
        logger.info("Frontend built successfully")
```

### 2.7 Конфигурация

**Дополнения в src/settings.py:**

```python
from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # Existing settings...
    
    # Frontend settings
    frontend_enabled: bool = Field(False, description="Enable frontend serving")
    frontend_build_on_start: bool = Field(True, description="Build frontend on server start")
    frontend_dev_mode: bool = Field(False, description="Enable frontend development mode")
    
    # Project customization
    project_path: Path | None = Field(None, description="Path to user project")
    project_static_path: Path | None = None
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.project_path:
            self.project_static_path = self.project_path / "static"
    
    class Config:
        env_prefix = "CLARINET_"
        env_file = ".env"
        toml_file = "settings.toml"
```

## 3. Этапы реализации

### Фаза 1: Базовая инфраструктура (2-3 дня)

1. **Создание структуры директорий frontend/**
   - Инициализация Gleam проекта
   - Настройка gleam.toml и manifest.toml
   - Создание базовой структуры папок

2. **CLI интеграция**
   - Добавление команд frontend в cli/main.py
   - Реализация install/build/clean команд
   - Тестирование команд

3. **Настройка конфигурации**
   - Расширение settings.py
   - Добавление frontend параметров
   - Обновление .env.example

### Фаза 2: API клиент и типизированные модели (3-4 дня)

1. **API клиент**
   - Базовый HTTP клиент в Gleam
   - Обработка JWT токенов
   - Типы для API ответов

2. **Статические модели данных**
   - Patient, Study, TaskDesign, User типы в api/models.gleam
   - Полная типизация всех основных сущностей
   - Decoders/Encoders для JSON
   - Валидация на уровне типов

3. **Аутентификация**
   - Login страница и форма
   - Сохранение токена в localStorage
   - Auto-refresh токенов
   - Logout функциональность

### Фаза 3: Основные компоненты UI и формы (4-5 дней)

1. **Layout компоненты**
   - Navbar с навигацией
   - Sidebar для меню
   - Footer
   - Container/Grid система

2. **Статические формы для моделей**
   - components/forms/patient_form.gleam
   - components/forms/study_form.gleam
   - components/forms/task_design_form.gleam
   - components/forms/user_form.gleam
   - Базовые элементы форм (base.gleam)

3. **Интеграция Formosh для Task.result**
   - components/formosh_wrapper.gleam
   - Загрузка Formosh web component
   - Обработка событий формы

4. **Другие компоненты**
   - Таблицы с сортировкой
   - Модальные окна
   - Уведомления/Toasts

5. **Стили**
   - base.css с переменными
   - Responsive дизайн
   - Темная/светлая тема

#### Пример навигации в компонентах с Modem

**src/frontend/src/components/navbar.gleam:**

```gleam
import lustre/element.{Element}
import lustre/element/html
import lustre/event
import lustre/attribute
import store.{Model, Msg}
import router

pub fn view(model: Model) -> Element(Msg) {
  html.nav([attribute.class("navbar")], [
    html.div([attribute.class("navbar-brand")], [
      link_to(router.Home, "Clarinet", is_active(model, router.Home))
    ]),
    html.div([attribute.class("navbar-menu")], [
      case model.user {
        Some(user) -> authenticated_menu(model, user)
        None -> guest_menu(model)
      }
    ])
  ])
}

fn link_to(route: router.Route, text: String, active: Bool) -> Element(Msg) {
  let classes = case active {
    True -> "navbar-item active"
    False -> "navbar-item"
  }
  
  html.a([
    attribute.href(router.route_to_path(route)),
    attribute.class(classes),
    event.on_click(fn(_) { store.Navigate(route) })
  ], [html.text(text)])
}

fn is_active(model: Model, route: router.Route) -> Bool {
  model.route == route
}

fn authenticated_menu(model: Model, user: User) -> Element(Msg) {
  html.div([attribute.class("navbar-items")], [
    link_to(router.Studies, "Studies", is_active(model, router.Studies)),
    link_to(router.Tasks, "Tasks", is_active(model, router.Tasks)),
    link_to(router.Users, "Users", is_active(model, router.Users)),
    html.div([attribute.class("navbar-user")], [
      html.span([], [html.text(user.username)]),
      html.button([
        attribute.class("btn-logout"),
        event.on_click(fn(_) { store.Logout })
      ], [html.text("Logout")])
    ])
  ])
}

fn guest_menu(model: Model) -> Element(Msg) {
  html.div([attribute.class("navbar-items")], [
    link_to(router.Login, "Login", is_active(model, router.Login))
  ])
}
```

### Фаза 4: Страницы приложения (4-5 дней)

1. **Studies модуль**
   - Список исследований с фильтрами
   - Детали исследования
   - Просмотр серий
   - Анонимизация

2. **Tasks модуль**
   - Список задач (list.gleam)
   - Детали задачи (detail.gleam)
   - Создание/редактирование TaskDesign (design.gleam - статическая форма)
   - Выполнение задачи с динамической формой (execute.gleam - использует Formosh)
   - История выполнения

3. **Users модуль**
   - Список пользователей (admin)
   - Профиль пользователя
   - Управление ролями
   - Настройки аккаунта

### Фаза 5: Интеграция с FastAPI (2-3 дня)

1. **Монтирование статики**
   - Обновление app.py
   - Настройка StaticFiles
   - SPA fallback роутинг

2. **CORS настройка**
   - Разрешение для фронтенда
   - Настройка для dev/prod

3. **Тестирование интеграции**
   - API вызовы из фронтенда
   - Аутентификация flow
   - Обработка ошибок

### Фаза 6: Пользовательская кастомизация (2 дня)

1. **Поддержка custom.css**
   - Загрузка пользовательских стилей
   - Переопределение переменных
   - Документация по кастомизации

2. **Обновление clarinet init**
   - Создание static/ директории
   - Примеры кастомизации
   - Шаблон custom.css

### Фаза 7: Тестирование и документация (2-3 дня)

1. **Тесты**
   - Unit тесты для Gleam модулей
   - E2E тесты основных flow
   - Тестирование сборки

2. **Документация**
   - README для фронтенда
   - Гайд по кастомизации
   - API клиента документация

## 4. Технические детали

### 4.1 Роутинг

Клиентский роутинг через Modem:

- `/` - Home/Dashboard
- `/login` - Авторизация
- `/studies` - Список исследований
- `/studies/:id` - Детали исследования
- `/tasks` - Список задач
- `/tasks/new` - Создание новой задачи
- `/tasks/:id` - Детали задачи
- `/users` - Управление пользователями
- `/users/:id` - Профиль пользователя

Modem обеспечивает:

- Декларативную навигацию через эффекты
- Автоматическую синхронизацию с browser history API
- Поддержку параметров маршрутов
- Обработку browser back/forward кнопок

### 4.2 State Management

Глобальное состояние в store.gleam:

- Текущий пользователь
- JWT токен
- Текущий роут
- Кэш загруженных данных
- UI состояние (модалы, уведомления)

**src/frontend/src/store.gleam:**

```gleam
import gleam/option.{Option, Some, None}
import gleam/dict.{Dict}
import lustre/effect.{Effect}
import modem
import router.{Route}
import api/types.{User}

pub type Model {
  Model(
    route: Route,
    user: Option(User),
    token: Option(String),
    loading: Bool,
    error: Option(String),
    cache: Dict(String, Dynamic)
  )
}

pub type Msg {
  OnRouteChange(Route)
  Navigate(Route)
  LoginSuccess(String, User)
  Logout
  SetLoading(Bool)
  SetError(Option(String))
  ClearError
}

pub fn init() -> Model {
  Model(
    route: router.Home,
    user: None,
    token: None,
    loading: False,
    error: None,
    cache: dict.new()
  )
}

pub fn set_route(model: Model, route: Route) -> Model {
  Model(..model, route: route)
}

pub fn set_auth(model: Model, token: String, user: User) -> Model {
  Model(..model, token: Some(token), user: Some(user))
}

// Middleware для проверки авторизации при навигации
pub fn check_auth_middleware(model: Model, route: Route) -> Effect(Msg) {
  case router.requires_auth(route), model.token {
    True, None -> {
      // Редирект на логин если нужна авторизация
      modem.replace("/login")
    }
    False, Some(_) if route == router.Login -> {
      // Редирект с логина если уже авторизован
      modem.replace("/")
    }
    _, _ -> effect.none()
  }
}
```

### 4.3 Обработка ошибок

- Network errors - повторные попытки с exponential backoff
- Auth errors - редирект на login
- Validation errors - показ в форме
- Server errors - уведомление пользователю

### 4.4 Безопасность

- JWT токены в localStorage
- HTTPS only в production
- CSP headers
- XSS защита через Lustre
- CSRF токены для форм

## 5. Зависимости

### Системные требования

- Gleam >= 1.4.0
- Erlang/OTP >= 26
- Node.js >= 18 (для сборки)

### Gleam пакеты

- lustre: UI фреймворк
- lustre_http: HTTP клиент
- lustre_ui: UI компоненты
- gleam_json: JSON работа
- modem: Клиентский роутинг с history API
- decipher: JSON декодеры

## 6. Конфигурация производительности

### Оптимизации сборки

- Tree shaking для уменьшения размера
- Минификация JavaScript
- Gzip компрессия статики
- Cache headers для статических файлов

### Runtime оптимизации

- Lazy loading страниц
- Виртуализация длинных списков
- Дебаунс для поисковых запросов
- Оптимистичные UI обновления

## 7. Мониторинг и отладка

### Development

- Hot reload через gleam build --watch
- Source maps для отладки
- Логирование API запросов
- Redux DevTools интеграция

### Production

- Error boundary для перехвата ошибок
- Sentry интеграция
- Performance метрики
- Аналитика использования

## 8. Расширяемость

### Плагины

- Система плагинов для кастомных компонентов
- API для регистрации новых страниц
- Hooks для расширения функциональности

### Темы

- CSS переменные для цветов
- Настраиваемые шрифты
- Кастомные иконки
- Брендинг через конфигурацию

## 9. Альтернативные решения

Если Gleam+Lustre окажется неподходящим:

1. **HTMX + Jinja2**
   - Server-side rendering
   - Минимум JavaScript
   - Проще для простых UI

2. **Vue.js/React**
   - Больше готовых компонентов
   - Большее сообщество
   - Лучше документация

3. **Elm**
   - Похож на Gleam по философии
   - Более зрелая экосистема
   - Строгая типизация

## 10. Риски и митигация

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Малая экосистема Gleam | Высокая | Среднее | Готовность писать свои компоненты |
| Сложность отладки | Средняя | Среднее | Source maps, логирование |
| Производительность | Низкая | Высокое | Профилирование, оптимизации |
| Совместимость браузеров | Низкая | Среднее | Полифиллы, тестирование |

## Заключение

План обеспечивает поэтапную интеграцию современного SPA фронтенда с минимальными изменениями в существующем коде.

### Ключевая особенность - Гибридный подход к формам

**Статические типизированные формы** для основных моделей (Patient, Study, TaskDesign, User):

- Полная типобезопасность на этапе компиляции
- Оптимальная производительность
- Прямое соответствие backend моделям
- IDE поддержка с автокомплитом

**Динамические формы через Formosh** только для Task.result:

- Автоматическая генерация из JSON Schema
- Поддержка произвольных структур данных
- Не требует изменения кода при добавлении новых типов задач
- Встроенная валидация

### Основные преимущества решения

- Баланс между типобезопасностью и гибкостью
- Функциональное программирование с Gleam/Lustre
- Минимальный runtime overhead
- Простота развертывания
- Гибкая кастомизация через CSS переменные

Ориентировочное время реализации: 3-4 недели для базовой функциональности, 5-6 недель для полной реализации с тестами и документацией.
