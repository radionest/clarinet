
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
