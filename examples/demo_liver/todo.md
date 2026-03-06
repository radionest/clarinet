# demo_liver: Отчёт по ревью конфигурации

## 1. RecordType конфигурации — ИСПРАВЛЕНО

Старый `record_types.toml` (все типы в одном файле) заменён на `tasks/` — отдельный файл на каждый RecordType, как в `examples/demo/tasks/`. Старая папка `schemas/` удалена, схемы теперь sidecar-файлы рядом с TOML.

Создано 11 RecordType конфигов + file_registry:
- `first_check.toml` + `.schema.json` (level=STUDY, исправлен с PATIENT)
- `segment_CT_single.toml` (role=doctor_CT)
- `segment_CT_with_archive.toml` (role=doctor_CT, lifecycle_open)
- `segment_MRI_single.toml` (role=doctor_MRI)
- `segment_CTAG_single.toml` (role=doctor_CT-AG)
- `segment_MRIAG_single.toml` (role=doctor_MRI)
- `segment_PDCTAG_single.toml` (role=doctor_PDCT)
- `create_master_projection.toml` (role=expert, input=master_model)
- `compare_with_projection.toml` + `.schema.json` (role=auto)
- `second_review.toml` + `.schema.json` (generic, classification output)
- `update_master_model.toml` (role=expert, level=PATIENT, max_records=1)
- `file_registry.toml` (shared file definitions)

Нейминг: min_records/max_records, segment_CT_single/segment_CT_with_archive. `settings.toml` обновлён: `recordflow_paths = ["./tasks"]`.

---

## 2. `files_catalog.py` — ИСПРАВЛЕНО

**Открытые вопросы**:
- Отсутствуют поля `role`, `required`, `description` из текущего `FileDefinition` clarinet. Поле `level` (PATIENT/STUDY/SERIES) — новое, нет в `FileDefinition`.
- Связь каталога с `RecordType.file_registry` — каталог проектный, RecordType ссылается по имени (как в `file_registry_resolver.py`).
- `level` определяет папку хранения и координатные гарантии. В текущем clarinet рабочая папка привязана к Record, а не к файлу — архитектурное расхождение, требующее рефакторинга.

---

## 3. `pipeline_flow.py` 

### Целевой DSL (отличия от текущего RecordFlow)

Следующие элементы DSL являются **целевыми** для рефакторинга clarinet:

| Целевой DSL | Текущий RecordFlow | Комментарий |
|---|---|---|
| `.on_finished()` | `.on_status('finished')` | Сокращение для частого случая |
| `.if_record(F.field == val)` | `.if_(record('x').data.field == val)` | `F` (Field) — лаконичнее; условие на ту же запись |
| `.create_record("a", "b")` | `.add_record("a")` | Поддержка создания нескольких записей за раз |
| `.do_task(fn)` | `.call(fn)` / `.pipeline('name')` | Единый метод для запуска задач |
| `file(x).on_update()` | Нет аналога | Триггер на изменение файла из каталога |
| `.invalidate_all_records("type")` | `.invalidate_records("type")` | Более явное название |
| `from clarinet.flow import ...` | `from src.services.recordflow import ...` | Публичный API пакета |

---

## 4. `README.md` — ИСПРАВЛЕНО

Бизнес-логика описана, вопросы закрыты. Оставшиеся открытые вопросы см. в самом README.md.

---

## 5. `settings.toml`

- `extra_roles` — нет такого параметра в текущем `settings.py`. Нужна реализация динамических ролей.
- `recordflow_paths = ["."]` — при запуске из другой директории разрешится некорректно.
- ~~`min_users` / `max_users` в настройках RecordType нужно переименовать в `min_records` / `max_records`.~~ Done.

---

## 6. Схемы JSON

**`first_check.schema.json`**: `best_series` — нет валидации формата DICOM UID (добавить `pattern`). `unevaluatedProperties: false` — JSON Schema 2019-09+, проверить поддержку валидатором clarinet.

**`compare_with_projection.schema.json`**: Асимметрия — `false_negative` массив объектов, `false_positive_num` просто число. Нет поля "требуется обновление мастер-модели" (решение принимается автоматически по `false_positive_num > 0`).

**Отсутствуют схемы** для: segment_CT_single, segment_CT_with_archive, segment_MRI_single, create_master_projection, second_review, update_master_model.

---

## 7. Новые концепции, требующие проектирования в clarinet

| Концепция | Где описана | Что нужно |
|---|---|---|
| Проектный каталог файлов с `level` | `files_catalog.py` | Расширить `FileDefinition` полем `level`; определить связь с `RecordType.file_registry` |
| `lifecycle_open` — кастомизация данных для просмотра | `segment_CT_with_archive.toml` | Python-скрипт возвращает доп. study_uid; `RecordRead.viewer_study_uids` computed-поле; frontend строит OHIF URL с несколькими StudyInstanceUIDs |
| `role` на RecordType — фильтр при назначении | `record_types.toml` | Механизм привязки ролей к типам записей |
| `role = "auto"` — автоматические записи | `record_types.toml` | Без user_id, видны в UI только для просмотра |
| `file().on_update()` — триггеры на файлы | `pipeline_flow.py` | Расширение RecordFlow DSL |
| `parent_record_id` — связь record-to-record | README.md | FK на Record, связь один-ко-многим |
| ~~`min_records` / `max_records` на RecordType~~ | README.md | ~~Замена min_users/max_users~~ Done |
| `image_processor` сервис | `pipeline_flow.py` | Обработка изображений (split_islands, has_overlap и т.д.) |
| Целевой DSL (`.on_finished()`, `F.field`, `.do_task()`) | `pipeline_flow.py` | Рефакторинг RecordFlow API |
| Event-driven проверка хэша мастер-модели при финише проекции | README.md | Хранение хэша input-файла в записи, сверка при завершении |
| `extra_roles` в settings | `settings.toml` | Динамические роли проекта |
| `second_review` — generic тип пересмотра | README.md | Новый RecordType с двухслойной разметкой в Slicer |
