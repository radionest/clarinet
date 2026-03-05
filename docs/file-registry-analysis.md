# File Registry — Архитектурный анализ

> Дата: 2026-03-05, ветка: `feature/file-registry`
> Контекст: анализ готовности фичи для реализации DSL из `examples/demo_liver/`

## Общая оценка

Фича хорошо спроектирована в основе: нормализованная схема M2M, дисциплина eager loading, async-safe computed fields на DTO. Но есть конкретные проблемы, ряд которых блокирует реализацию `demo_liver`.

---

## 1. DRY — нарушения

### 1.3 Извлечение `fd_data` из разных форматов

`record.py:240-252` строит `fd_data` через isinstance-проверки (`FileDefinitionRead` vs `dict`), а `reconciler.py:193-201` делает то же самое но только для dict. Два разных способа извлечь `{name, pattern, description, multiple}`.

---

## 2. KISS — избыточная сложность

### 2.1 `FileDefinitionRead` — "god DTO"

Один класс обслуживает 6 контекстов:
- API response (RecordTypeRead.file_registry)
- Config reconciler input
- File validation input (FileValidator)
- Pattern resolution (resolve_pattern)
- Checksum computation (compute_checksums)
- Config primitives conversion (fileref_to_file_definition)

Пока это работает, но при добавлении `level` (нужно для demo_liver) этот DTO станет ещё тяжелее. Нет разделения между "что API показывает" и "что утилиты потребляют".


---

## 3. YAGNI — лишнее

### 3.1 `FileRole.INTERMEDIATE`

Определён (`file_schema.py:30`), но нигде не используется. demo_liver его не использует. Нет consumer'а — мёртвый код.

### 3.2 `FileReference` наследует `SQLModel`

`file_registry_resolver.py:21-32` — используется только как парсер в `resolve_file_references()`. Достаточно `TypedDict` или `dataclass`. `SQLModel` без `table=True` — Pydantic с лишним весом.

### 3.3 JSON fallback для file registry

`load_project_file_registry` поддерживает и TOML и JSON (`file_registry_resolver.py:52-64`). Все примеры и docs используют TOML. JSON-путь — мёртвый код.

### 3.4 Deprecated `RecordRead.files` / `file_checksums`

Вычисляются и отдаются в каждом API-ответе наряду с `file_links`. Дублирование данных в response body + вычислительная нагрузка ради backward compat без плана удаления.

---

## 4. Протечки абстракций

### 4.1 ORM-логика в роутере

`POST /types` (`record.py:231-271`) напрямую:
- Создаёт `RecordType` ORM-объект
- Делает `session.add()` и `session.flush()`
- Строит `RecordTypeFileLink` объекты
- Вызывает `session.commit()`

Роутер выполняет работу, которая должна быть в `RecordTypeRepository.create_with_files()` или сервисе. Нарушение слоёв.

### 4.2 Тихий fallback при отсутствии eager loading

`RecordType.file_registry` property (record_type.py) возвращает `[]` если `file_links` не загружены. Это прячет баги — вызывающий код получает пустой реестр вместо ошибки и продолжает работу с неполными данными.

### 4.3 `validate_record_files` принимает `RecordRead`, не `Record`

`record.py:109-146` — функция работает с `RecordRead` DTO потому что `working_folder` — computed field. Это протечка: бизнес-логика валидации привязана к DTO API-слоя. Решение — вычислять `working_folder` отдельно или сделать его доступным на ORM-уровне.

### 4.4 `compute_checksums` требует `RecordBase` для resolve_pattern

`file_checksums.py:43-76` — принимает `record: RecordBase` чтобы передать в `resolve_pattern()`. Но для файлов без placeholder'ов (как `master_model.seg.nii`) record не нужен. Утилита привязана к модели без необходимости.

---

## 5. Bottlenecks


### 5.2 Delete-all + recreate при каждом обновлении

`set_files()` (`record_repository.py:336-338`) и `_sync_file_links()` (`reconciler.py:184-186`) удаляют ВСЕ существующие links, затем создают заново. При обновлении 1 из 10 файлов — 10 DELETE + 10 INSERT вместо 1 UPDATE. Также провоцирует лишние CASCADE-проверки.

### 5.3 `update_checksums` загружает полный Record

`record_repository.py:307` — `get_with_record_type(record_id)` загружает Record + RecordType + file_links + file_definitions, чтобы обновить 1 поле. Можно сделать одним `UPDATE ... WHERE record_id = ? AND file_definition_id IN (...)`.

---

## 6. Архитектурные gaps для demo_liver

### 6.1 Нет `level` на `FileDefinition` (КРИТИЧНО)

demo_liver определяет:
```python
master_model = File(pattern="master_model.seg.nii", level="PATIENT")
segmentation_single = File(pattern="seg_{user_id}.seg.nrrd", level="SERIES")
```

`FileDefinition` в БД не хранит `level`. `File` в `primitives.py:28` имеет `level: str | None`, но `fileref_to_file_definition()` его **теряет** — не включает в `FileDefinitionRead`. Без `level` невозможен cross-level file access.

### 6.2 Нет cross-level file access

`create_master_projection` — SERIES-level task, но его INPUT `master_model` хранится на уровне PATIENT. Текущий `validate_record_files` и `compute_checksums` работают с единственным `working_folder` record'а. Нет механизма разрешения пути файла по его уровню.

### 6.3 Нет `ctx.files` API для pipeline tasks

`pipeline_flow.py` использует:
```python
ctx.files.exists(master_model)
ctx.files.get(segmentation_single, uid=msg["series_uid"])
ctx.files.get_path(master_model)
```
Такого API не существует. Это отдельная фича, но file registry должен предоставлять данные для неё.

### 6.4 Нет `file().on_update()` trigger в RecordFlow

`pipeline_flow.py:178` — `file(master_model).on_update().invalidate_all_records(...)`. Текущий RecordFlow DSL не поддерживает file-level триггеры. `handle_record_file_change` реагирует на checksum-изменения record'а, но не на изменения конкретного файла по имени.

---

## 7. Резюме и приоритеты

| Категория | Проблема | Приоритет | Усилие |
|---|---|---|---|
| DRY | File link creation x3 | Средний | Низкое |
| DRY | Duplicate name validator | Низкий | Тривиальное |
| KISS | isinstance в POST /types | Средний | Низкое |
| KISS | Sync I/O в async | Средний | Среднее |
| YAGNI | FileRole.INTERMEDIATE | Низкий | Тривиальное |
| YAGNI | JSON fallback | Низкий | Тривиальное |
| Абстракции | ORM-логика в роутере | Средний | Среднее |
| Абстракции | `validate_record_files` на RecordRead | Средний | Среднее |
| Bottleneck | Sequential checksums | Средний | Низкое |
| Bottleneck | Delete-all + recreate | Низкий | Среднее |
| **Gap** | **`level` на FileDefinition** | **Критический** | **Среднее** |
| **Gap** | **Cross-level file access** | **Критический** | **Высокое** |
| **Gap** | **`ctx.files` API** | **Высокий** | **Высокое** |
| **Gap** | **`file().on_update()` trigger** | **Высокий** | **Высокое** |

---

## 8. Рекомендуемый порядок действий

1. **Рефакторинг DRY** (file link creation → общий метод) — чтобы не множить дублирование
2. **`level` на FileDefinition** — миграция + DTO + config loader
3. **Cross-level file path resolution** — новая утилита `resolve_file_storage_path()`
4. **Обновить FileValidator** для multi-directory валидации
5. **`ctx.files` API** для pipeline tasks
6. **`file().on_update()` trigger** в RecordFlow DSL

Шаги 1-4 — фундамент. Шаги 5-6 — надстройка для полной поддержки demo_liver DSL.
