# План рефакторинга системы сборки фронтенда Clarinet

## Обзор

Фронтенд Clarinet написан на языке Gleam и требует компиляции в JavaScript для использования в браузере. Данный документ описывает план организации процесса сборки, который будет работать как в среде разработки, так и через CI/CD на GitHub.

## Текущая ситуация

- Фронтенд находится в `src/frontend/`
- Используется Gleam с целевой платформой JavaScript
- Конфигурация в `src/frontend/gleam.toml`
- Основные зависимости: Lustre (UI фреймворк), Modem (роутинг), Formosh (формы)

## Цели рефакторинга

1. Автоматизировать сборку фронтенда через GitHub Actions
2. Обеспечить единообразие сборки в dev и production окружениях
3. Включить собранные файлы в pip-пакет без требования Gleam у пользователя
4. Поддержать возможность кастомизации стилей на стороне пользователя

## Архитектурное решение

### Структура директорий

```
clarinet/                    # Корень репозитория библиотеки
├── src/                    # Python пакет исходники
│   ├── api/               # FastAPI роуты
│   ├── models/            # SQLModel модели
│   ├── services/          # Бизнес-логика
│   ├── utils/             # Утилиты
│   └── ...
├── dist/                   # Собранный фронтенд (генерируется, в .gitignore локально)
│   ├── index.html         # Скопирован из frontend/public/
│   ├── js/                # Скомпилированный JavaScript
│   │   └── app.mjs
│   ├── css/               # Стили
│   └── assets/            # Изображения и другие ресурсы
├── frontend/               # Исходники Gleam фронтенда
│   ├── src/               # Компоненты и страницы Gleam
│   ├── public/            # Статические файлы
│   │   ├── index.html     # HTML шаблон
│   │   ├── css/           # CSS файлы
│   │   └── assets/        # Изображения, шрифты
│   ├── build/             # Временная директория Gleam (в .gitignore)
│   └── gleam.toml         # Конфигурация Gleam
├── scripts/
│   └── build_frontend.sh  # Скрипт локальной сборки
├── .github/
│   └── workflows/
│       └── frontend-build.yml # CI workflow
└── Makefile               # Унифицированные команды

# У пользователя после pip install:
site-packages/
└── clarinet/
    └── dist/              # Собранный фронтенд включен в пакет
        ├── index.html
        ├── js/
        ├── css/
        └── assets/

# Проект пользователя:
user_project/
├── clarinet_custom/       # Опциональная кастомизация
│   ├── styles.css        # Дополнительные стили
│   └── config.json       # Переопределение настроек
```

## Процесс сборки

### 1. Локальная разработка

#### Скрипт сборки: `scripts/build_frontend.sh`

```bash
#!/bin/bash
set -e

echo "Building Clarinet frontend..."

# Переход в директорию фронтенда
cd frontend

# Загрузка зависимостей
gleam deps download

# Компиляция Gleam в JavaScript
gleam build --target javascript

# Очистка и создание директории dist
rm -rf ../dist
mkdir -p ../dist/{js,css,assets}

# Копирование собранного JavaScript модуля
cp build/dev/javascript/clarinet.mjs ../dist/js/app.mjs

# Копирование всех статических файлов из public
cp -r public/* ../dist/

echo "Frontend build complete! Output in dist/"
```

#### Команды разработчика через CLI

```bash
# Запуск сервера с фронтендом (FastAPI отдает статику из dist/)
clarinet run --with-frontend

# Сборка фронтенда
clarinet frontend build

# Сборка с отслеживанием изменений
clarinet frontend build --watch

# Очистка артефактов сборки
clarinet frontend clean

# Через Makefile
make frontend-build  # Production сборка в dist/
make run-dev        # Запуск FastAPI с фронтендом
```

### 2. CI/CD через GitHub Actions

#### Workflow: `.github/workflows/frontend-build.yml`

```yaml
name: Build Frontend

on:
  push:
    branches: [main, develop]
    paths:
      - 'frontend/**'
      - '.github/workflows/frontend-build.yml'
      - 'scripts/build_frontend.sh'
  pull_request:
    paths:
      - 'frontend/**'

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Gleam
        uses: erlef/setup-beam@v1
        with:
          gleam-version: "1.5.0"
          otp-version: "26.0"

      - name: Cache Gleam dependencies
        uses: actions/cache@v3
        with:
          path: |
            frontend/build/packages
            frontend/build/dev
          key: gleam-deps-${{ runner.os }}-${{ hashFiles('frontend/gleam.toml', 'frontend/manifest.toml') }}
          restore-keys: |
            gleam-deps-${{ runner.os }}-

      - name: Build frontend
        run: |
          cd frontend
          gleam deps download
          gleam build --target javascript

      - name: Generate dist directory
        run: |
          rm -rf dist
          mkdir -p dist/{js,css,assets}
          cp frontend/build/dev/javascript/clarinet.mjs dist/js/app.mjs
          cp -r frontend/public/* dist/

      - name: Commit and push changes
        if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        run: |
          git config --global user.name "GitHub Actions"
          git config --global user.email "actions@github.com"
          git add dist
          if git diff --staged --quiet; then
            echo "No changes to commit"
          else
            git commit -m "chore: update frontend build [skip ci]"
            git push
          fi
```

### 3. Makefile для унификации

```makefile
# Frontend commands
.PHONY: frontend-build frontend-clean frontend-test run-dev

# Production сборка фронтенда
frontend-build:
	@echo "Building frontend..."
	@cd frontend && gleam deps download && gleam build --target javascript
	@echo "Generating dist directory..."
	@rm -rf dist
	@mkdir -p dist/js dist/css dist/assets
	@cp frontend/build/dev/javascript/clarinet.mjs dist/js/app.mjs
	@cp -r frontend/public/* dist/
	@echo "Frontend build complete! Output in dist/"

# Запуск сервера разработки (FastAPI + фронтенд)
run-dev:
	clarinet run --with-frontend

# Очистка build артефактов
frontend-clean:
	rm -rf frontend/build
	rm -rf dist

# Тесты фронтенда
frontend-test:
	cd src/frontend && gleam test

# Полная сборка пакета (backend + frontend)
build: frontend-build
	python -m build

# Установка зависимостей для разработки
dev-setup:
	pip install -e ".[dev]"
	clarinet frontend install
```

## Интеграция с Python пакетом

### Конфигурация `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["clarinet"]
package-dir = {"" = "src"}

[tool.setuptools.package-data]
clarinet = [
    "dist/**/*",
    "dist/js/*",
    "dist/css/*",
    "dist/assets/*"
]
```

### Обновление `src/clarinet/settings.py`

```python
from pathlib import Path
from typing import Optional

class Settings(BaseSettings):
    # ... существующие настройки ...

    # Пути к статическим файлам
    @property
    def static_path(self) -> Path:
        """Путь к встроенным статическим файлам"""
        return Path(__file__).parent / "dist"

    @property
    def custom_static_path(self) -> Optional[Path]:
        """Путь к пользовательским статическим файлам"""
        custom_path = Path.cwd() / "clarinet_custom"
        return custom_path if custom_path.exists() else None

    @property
    def static_directories(self) -> list[Path]:
        """Список директорий со статикой в порядке приоритета"""
        dirs = []
        if self.custom_static_path:
            dirs.append(self.custom_static_path)
        dirs.append(self.static_path)
        return dirs
```

### FastAPI интеграция

```python
# src/api/app.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

app = FastAPI()

# API роуты
app.include_router(auth_router, prefix="/api")
app.include_router(study_router, prefix="/api")
app.include_router(task_router, prefix="/api")

# Монтирование статических файлов
if settings.frontend_enabled:
    # Приоритет: сначала кастомные файлы, потом встроенные
    for static_dir in settings.static_directories:
        if static_dir.exists():
            app.mount(
                "/static",
                StaticFiles(directory=str(static_dir)),
                name=f"static_{static_dir.name}"
            )

    # Отдача index.html для всех маршрутов фронтенда
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve SPA for all non-API routes"""
        if full_path.startswith("api/"):
            return {"error": "Not found"}, 404

        index_path = settings.static_path / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return {"error": "Frontend not built"}, 404
```

## Поддержка кастомизации

### Структура пользовательского проекта

```
user_project/
├── clarinet_custom/
│   ├── styles.css       # Дополнительные/переопределяющие стили
│   ├── theme.json       # Настройки темы (цвета, шрифты)
│   └── logo.svg         # Кастомный логотип
├── settings.toml        # Настройки Clarinet
└── main.py             # Точка входа
```

### Пример `clarinet_custom/theme.json`

```json
{
  "colors": {
    "primary": "#1e40af",
    "secondary": "#64748b",
    "accent": "#f59e0b"
  },
  "fonts": {
    "body": "Inter, sans-serif",
    "heading": "Poppins, sans-serif"
  },
  "logo": "logo.svg"
}
```

## Процесс релиза

1. **Разработка**: Изменения во фронтенде в feature ветках
2. **Pull Request**: CI собирает фронтенд и проверяет сборку
3. **Merge в main**: CI автоматически обновляет `dist/`
4. **Релиз**:
   - Тег версии запускает полную сборку
   - Публикация в PyPI включает собранные статические файлы
   - Пользователи получают готовый к использованию пакет

## Преимущества подхода

1. **Нулевые зависимости**: Пользователям не нужен Gleam/Node.js
2. **Версионирование**: Фронтенд и бэкенд синхронизированы
3. **Кастомизация**: Простое переопределение стилей и настроек
4. **Автоматизация**: CI/CD полностью автоматизирован
5. **Кеширование**: Ускорение сборки за счет кеша зависимостей
6. **Унификация**: Одинаковые команды для всех окружений

## Команды для разработчиков

```bash
# Первичная настройка
make dev-setup

# Ежедневная разработка
make run-dev           # Запуск FastAPI с фронтендом (http://localhost:8000)
make frontend-build    # Локальная production сборка
make frontend-test     # Запуск тестов
make frontend-clean    # Очистка build файлов

# CLI команды
clarinet run --with-frontend    # Запуск с фронтендом
clarinet frontend install       # Установка Gleam и зависимостей
clarinet frontend build         # Сборка фронтенда
clarinet frontend build --watch # Сборка с отслеживанием
clarinet frontend clean         # Очистка артефактов

# Полная сборка перед коммитом
make build            # Сборка фронтенда и Python пакета
make test             # Все тесты (backend + frontend)
```

## Миграция с текущей системы

1. Создать директорию `dist/` (автоматически при сборке)
2. Добавить `dist/` в `.gitignore` для локальной разработки
3. Реализовать скрипт `scripts/build_frontend.sh`
4. Настроить GitHub Actions workflow
5. Обновить `pyproject.toml` для включения статики
6. Обновить документацию

## Потенциальные проблемы и решения

| Проблема | Решение |
|----------|---------|
| Большой размер репозитория | Использовать Git LFS для больших файлов |
| Конфликты при мерже static файлов | Добавить `[skip ci]` в коммиты CI |
| Долгая сборка в CI | Агрессивное кеширование зависимостей |
| Различия в версиях Gleam | Зафиксировать версию в CI и документации |

## Ключевые изменения от исходного плана

1. **Без lustre/dev start**: Фронтенд не запускается как отдельный сервер, вместо этого FastAPI отдает статические файлы
2. **Единая точка входа**: Весь трафик идет через FastAPI сервер на порту 8000
3. **CLI интеграция**: Используется существующая команда `clarinet run --with-frontend`
4. **SPA поддержка**: FastAPI корректно обрабатывает маршруты фронтенда, отдавая index.html
5. **API изоляция**: Все API эндпоинты находятся под префиксом `/api`

## Важное примечание о создании index.html

**index.html должен находиться в `frontend/public/index.html`** и содержать:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Clarinet - Medical Imaging Platform</title>
    <link rel="stylesheet" href="/static/css/main.css">
</head>
<body>
    <div id="app"></div>
    <script type="module">
        import { main } from '/static/js/app.mjs';
        main();
    </script>
</body>
</html>
```

Этот файл будет копироваться как есть в `dist/` при сборке.

## Заключение

Данный план обеспечивает надежную и автоматизированную систему сборки фронтенда, где:
- Папка `dist/` полностью генерируется из исходников
- `frontend/public/` содержит все статические файлы включая `index.html`
- FastAPI сервер является единой точкой входа для API и статических файлов
- Структура избегает путаницы с именованием (нет вложенных папок clarinet)

Это упрощает деплой и разработку, работает единообразно во всех окружениях и не требует от конечных пользователей установки инструментов разработки.