# План интеграции Gleam+Lustre фронтенда в Clarinet

## Обзор

Интеграция SPA фронтенда на Gleam+Lustre в существующий FastAPI backend. Фронтенд будет опциональным модулем, обслуживаемым через основной FastAPI сервер, с поддержкой пользовательской кастомизации стилей.

## 1. Архитектура

### 1.1 Структура директорий

```
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
│   │   │   │   └── types.gleam
│   │   │   ├── components/     # Переиспользуемые компоненты
│   │   │   │   ├── layout.gleam
│   │   │   │   ├── navbar.gleam
│   │   │   │   ├── forms.gleam
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
│   │   │   │   │   └── form.gleam
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
decipher = "~> 1.0"  # Для работы с JSON
rada = "~> 1.0"      # Роутинг

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
import lustre/cmd.{Cmd}
import router
import store.{Model, Msg}
import pages/login
import pages/home
import components/layout

pub fn main() {
  let app = lustre.application(init, update, view)
  let assert Ok(_) = lustre.start(app, "#app", Nil)
  Nil
}

fn init(_) -> #(Model, Cmd(Msg)) {
  let model = store.init()
  #(model, cmd.none())
}

fn update(model: Model, msg: Msg) -> #(Model, Cmd(Msg)) {
  case msg {
    store.Navigate(route) -> {
      let new_model = store.set_route(model, route)
      #(new_model, router.push(route))
    }
    store.LoginSuccess(token, user) -> {
      let new_model = model
        |> store.set_auth(token, user)
        |> store.set_route(router.Home)
      #(new_model, router.push(router.Home))
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

### 2.4 Интеграция с FastAPI

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

### 2.5 CLI команды

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

### 2.6 Конфигурация

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

### Фаза 2: API клиент и аутентификация (3-4 дня)

1. **API клиент**
   - Базовый HTTP клиент в Gleam
   - Обработка JWT токенов
   - Типы для API ответов

2. **Модели данных**
   - User, Study, Task, Patient типы
   - Decoders/Encoders для JSON
   - Валидация данных

3. **Аутентификация**
   - Login страница и форма
   - Сохранение токена в localStorage
   - Auto-refresh токенов
   - Logout функциональность

### Фаза 3: Основные компоненты UI (3-4 дня)

1. **Layout компоненты**
   - Navbar с навигацией
   - Sidebar для меню
   - Footer
   - Container/Grid система

2. **Базовые компоненты**
   - Формы (Input, Select, Textarea)
   - Таблицы с сортировкой
   - Модальные окна
   - Уведомления/Toasts

3. **Стили**
   - base.css с переменными
   - Responsive дизайн
   - Темная/светлая тема

### Фаза 4: Страницы приложения (4-5 дней)

1. **Studies модуль**
   - Список исследований с фильтрами
   - Детали исследования
   - Просмотр серий
   - Анонимизация

2. **Tasks модуль**
   - Список задач
   - Создание/редактирование задач
   - Форма выполнения задачи
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

Клиентский роутинг через rada или lustre_router:
- `/` - Home/Dashboard
- `/login` - Авторизация
- `/studies` - Список исследований
- `/studies/:id` - Детали исследования
- `/tasks` - Список задач
- `/tasks/:id` - Детали задачи
- `/users` - Управление пользователями
- `/profile` - Профиль текущего пользователя

### 4.2 State Management

Глобальное состояние в store.gleam:
- Текущий пользователь
- JWT токен
- Текущий роут
- Кэш загруженных данных
- UI состояние (модалы, уведомления)

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

### Системные требования:
- Gleam >= 1.4.0
- Erlang/OTP >= 26
- Node.js >= 18 (для сборки)

### Gleam пакеты:
- lustre: UI фреймворк
- lustre_http: HTTP клиент
- lustre_ui: UI компоненты
- gleam_json: JSON работа
- rada: Роутинг
- decipher: JSON декодеры

## 6. Конфигурация производительности

### Оптимизации сборки:
- Tree shaking для уменьшения размера
- Минификация JavaScript
- Gzip компрессия статики
- Cache headers для статических файлов

### Runtime оптимизации:
- Lazy loading страниц
- Виртуализация длинных списков
- Дебаунс для поисковых запросов
- Оптимистичные UI обновления

## 7. Мониторинг и отладка

### Development:
- Hot reload через gleam build --watch
- Source maps для отладки
- Логирование API запросов
- Redux DevTools интеграция

### Production:
- Error boundary для перехвата ошибок
- Sentry интеграция
- Performance метрики
- Аналитика использования

## 8. Расширяемость

### Плагины:
- Система плагинов для кастомных компонентов
- API для регистрации новых страниц
- Hooks для расширения функциональности

### Темы:
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

План обеспечивает поэтапную интеграцию современного SPA фронтенда с минимальными изменениями в существующем коде. Основные преимущества:

- Полная типобезопасность
- Функциональное программирование
- Минимальный runtime overhead
- Простота развертывания
- Гибкая кастомизация

Ориентировочное время реализации: 3-4 недели для базовой функциональности, 5-6 недель для полной реализации с тестами и документацией.