# Clarinet Research Project

Это шаблон клинико-радиологического исследования на фреймворке [clarinet](https://github.com/...). Документ всегда загружается в контекст агента и даёт обзор проекта; детали по каждому разделу — в `.claude/rules/*.md` (автоматически подключаются при редактировании файлов в соответствующих папках).

> Замените содержимое этого файла под своё исследование (название, описание, спецификации). Структура и API-ссылки ниже остаются актуальными.

## Что это за проект

`<Название исследования>` — `<краткое медицинское описание>`. Цель: `<гипотеза / диагностический endpoint>`.

Источник истины для метаданных проекта — `settings.toml` (имя, описание, base URL, кастомные роли).

## Архитектура за 30 секунд

Clarinet моделирует исследовательский pipeline через четыре сущности:

- **DICOM-иерархия** Patient → Study → Series — фреймворк сам импортирует исследования из PACS и анонимизирует.
- **RecordType** — типизированный шаг workflow, привязанный к уровню иерархии (PATIENT/STUDY/SERIES). Описывает: какой роли врача показывать запись, какие файлы создаются как input/output, какая форма данных, какой Slicer-скрипт открывать.
- **RecordFlow DSL** — декларативная оркестровка переходов между записями: «когда исследование пришло → создать first-check», «когда сегментация завершена → запустить projection и сравнение», «при изменении master-модели → инвалидировать зависимые записи».
- **Pipeline tasks** — асинхронные функции, выполняющиеся в воркерах (RabbitMQ + TaskIQ): тяжёлая работа с DICOM, конвертация в NIfTI, обработка изображений, GPU-инференс.

Файлы привязаны к уровню DICOM-иерархии и резолвятся по паттернам с плейсхолдерами (`{study_uid}`, `{user_id}`, ...).

## Структура каталогов

```
plan/
├── definitions/      # FileDef + RecordDef — единственное место объявления типов
├── workflows/        # @pipeline_task функции + RecordFlow DSL
├── hydrators/        # Async-функции, инжектирующие переменные в Slicer-скрипты
├── scripts/          # Bare Python-скрипты для 3D Slicer (интерактивная работа)
├── validators/       # Bare Python-валидаторы, выполняющиеся после Slicer-задачи
├── schemas/          # JSON Schema для record.data (валидация + UI-формы)
└── utils/            # Проектно-специфичные helper-модули
```

Каждой подпапке соответствует rule-файл в `.claude/rules/` с подробными конвенциями.

## API clarinet — что откуда импортировать

| Что нужно | Откуда |
|---|---|
| `FileDef`, `FileRef`, `RecordDef` | `clarinet.flow` |
| `pipeline_task`, `PipelineMessage`, `TaskContext`, `SyncTaskContext` | `clarinet.services.pipeline` |
| `record`, `series`, `study`, `patient`, `file`, `Field` | `clarinet.services.recordflow` |
| `slicer_context_hydrator`, `SlicerHydrationContext` | `clarinet.services.slicer.context_hydration` |
| `SlicerHelper` | `clarinet.services.slicer.helper` |
| `ClarinetClient` | `clarinet.client` |
| `RecordCreate`, `RecordRead`, `RecordStatus` | `clarinet.models` (RecordStatus — из `clarinet.models.base`) |
| `RecordSearchCriteria` | `clarinet.repositories.record_repository` |
| `Segmentation` (numpy/nrrd-обёртка) | `clarinet.services.image` |
| `logger` | `clarinet.utils.logger` (никогда не импортировать loguru напрямую) |
| `settings` | `clarinet.settings` |

## Ключевые настройки `settings.toml`

```toml
config_mode = "python"                                 # Python-режим конфигурации
config_tasks_path = "./plan/"                          # Корневая папка для остальных путей
config_record_types_file = "definitions/record_types.py"
config_context_hydrators_file = "hydrators/context_hydrators.py"
recordflow_paths = ["./plan/workflows"]                # Где искать *_flow.py

recordflow_enabled = true                              # Включить движок RecordFlow
pipeline_enabled = true                                # Включить TaskIQ-брокер (нужен RabbitMQ)
frontend_enabled = true                                # Подключить frontend SPA

extra_roles = ["doctor_CT", "surgeon"]                 # Кастомные роли поверх admin/user
```

Все пути указываются **относительно `config_tasks_path`** (то есть `plan/`). Любая роль, упомянутая в `RecordDef.role`, должна быть в `extra_roles` (или быть стандартной: `admin`, `user`, `doctor`, `auto`, `expert`).

## Основные команды

```bash
cp .env.example .env                          # Заполнить секретами
uv run clarinet db init                       # Инициализация БД + создание admin
uv run clarinet run                           # Запуск API + frontend
uv run clarinet worker                        # Pipeline воркер (все очереди)
uv run clarinet worker --queues clarinet.dicom  # Конкретные очереди
uv run clarinet ohif install                  # Установить OHIF Viewer (на /ohif)
uv run clarinet rabbitmq status               # Состояние очередей
```

Полный список — `uv run clarinet --help` и `make help` в репозитории фреймворка.

## Конвенции именования

- **`RecordDef.name`** — kebab-case, 5-30 символов: `"first-check"`, `"segment-ct-single"`. Это идентификатор в DSL и URL.
- **Python-переменные** — snake_case: `first_check = RecordDef(name="first-check", ...)`. Имя переменной может отличаться от `name`.
- **Файлы scripts/validators** — snake_case, валидаторы с суффиксом `_validator`: `segment.py`, `segment_validator.py`.
- **Schema-файлы** — `{record-type-name}.schema.json`: `first-check.schema.json` (kebab-case под `RecordDef.name`).
- **Hydrator injection key** — snake_case: `@slicer_context_hydrator("best_series_from_first_check")`.

## Сквозные правила

- **Идемпотентность pipeline-тасок**. Каждая задача обязана проверять `ctx.files.exists(output_file_def)` и выходить рано, если результат уже есть. Причина: ретраи воркера, ручные перезапуски, cascade-инвалидация могут вызвать таску повторно.
- **Логирование** — только `from clarinet.utils.logger import logger` с f-strings. Никогда `print()` и никогда `import loguru`.
- **Slicer-скрипты — bare Python** в окружении 3D Slicer. Глобалы (`slicer`, `working_folder`, `output_file`, ...) инжектируются фреймворком; в начале каждого скрипта обязателен docstring с перечислением context vars.
- **Async vs sync в pipeline-тасках**. Async — для I/O, HTTP, БД (ClarinetClient). Sync (`SyncTaskContext`) — для CPU-bound работы (skimage, SimpleITK, vtk); такие функции автоматически выполняются в треде.
- **`asyncio.gather` запрещён на shared `ClarinetClient`/AsyncSession** — параллельные запросы блокируют друг друга на одном connection. Используйте последовательный `await` или создавайте отдельные клиенты.

## Куда смотреть за деталями

Rules этого проекта (автозагрузка по `paths` в frontmatter):

- `.claude/rules/definitions.md` — `FileDef`, `RecordDef`, паттерны путей, связи между разделами
- `.claude/rules/workflows.md` — `@pipeline_task`, `TaskContext`, RecordFlow DSL
- `.claude/rules/slicer.md` — hydrators + Slicer-скрипты + валидаторы (всё связано через injection vars)
- `.claude/rules/schemas.md` — JSON Schema для record.data, conditional schemas, UI-хинты
- `.claude/rules/utils.md` — helper-модули, формат `.seg.nrrd`

Rules фреймворка (полные reference-доки, лежат в самом репозитории clarinet — полезны как справочник):

- `<clarinet>/clarinet/.claude/rules/recordflow-dsl.md` — полный API DSL с pattern matching
- `<clarinet>/clarinet/.claude/rules/slicer-helper-api.md` — все методы `SlicerHelper` + VTK pitfalls
- `<clarinet>/clarinet/.claude/rules/pipeline-ops.md` — настройки pipeline, тестирование, очереди
- `<clarinet>/clarinet/.claude/rules/file-registry.md` — детали резолвинга паттернов файлов
- `<clarinet>/clarinet/.claude/rules/project-setup.md` — обзор шаблонов, опции `clarinet init`

Production-пример: репозиторий `clarinet_nir_liver` (если доступен) — самое полное реальное использование шаблона.
