# MissingGreenlet при сериализации ответов + остатки старых БД

## Проблема 4: Старые файлы БД не удаляются при перезапуске

### Описание

При повторных запусках демо-сервера с разными настройками (разные `database_name`, разные рабочие директории) создаются файлы SQLite в разных местах:

- `examples/demo/clarinet.db` (дефолтное имя)
- `examples/demo/clarinet_demo.db` (из `settings.toml`)
- `examples/clarinet.db` (если CWD — `examples/`)
- `clarinet.db` (если CWD — корень проекта)

При следующем запуске сервер может подключиться к старой БД с уже существующими данными. Скрипт `generate_test_data.py` получает 409 Conflict при создании пациентов, `created_patients` остаётся пустым, и дальнейшие шаги (studies, series, records) пропускаются.

### Воспроизведение

```bash
# Первый запуск — данные создаются
python scripts/generate_test_data.py

# Второй запуск — все пациенты уже есть
python scripts/generate_test_data.py
#   Patient PAT001 error: API error: 409
#   Patient PAT002 error: API error: 409
#   ...
# Studies и series не создаются (created_patients пуст)
```

### Предлагаемое решение

1. **В `generate_test_data.py`**: при 409 — получать существующего пациента через `get_patients()` или отдельный endpoint, вместо пропуска.

2. **В README**: добавить инструкцию по очистке БД перед повторным запуском:
   ```bash
   rm -f examples/demo/*.db
   ```

3. **В CLI**: добавить команду `clarinet db reset` для пересоздания БД.

---

## Проблема 5: ResponseValidationError / MissingGreenlet при создании records

### Описание

При вызове `POST /api/records` (создание записи) и `GET /api/patients` (получение пациентов) сервер возвращает 500. Причина — `MissingGreenlet` при сериализации ответа.

FastAPI пытается сериализовать вложенные связи (patient -> studies -> series) через response model. SQLAlchemy пытается лениво загрузить эти связи, но в async-контексте lazy loading невозможен без greenlet — возникает ошибка.

### Полный traceback

```
fastapi.exceptions.ResponseValidationError: 7 validation errors:
  {'type': 'get_attribute_error',
   'loc': ('response', 0, 'studies', 0, 'series'),
   'msg': "Error extracting attribute: MissingGreenlet: greenlet_spawn
           has not been called; can't call await_only() here.
           Was IO attempted in an unexpected place?",
   'input': Study(study_uid='1.2.840.11111.1.1', ...)}
```

### Затронутые endpoints

- `POST /api/records` — создание записи (record создаётся, но ответ падает)
- `GET /api/patients` — получение списка пациентов

### Корневая причина

В роутерах/репозиториях при запросе данных не используется eager loading для вложенных связей. SQLAlchemy в async-режиме не поддерживает lazy loading — все связи должны быть загружены явно через `selectinload()` или `joinedload()`.

### Где смотреть

1. **Response models** — проверить, какие вложенные связи включены:
   - `src/models/patient.py` — `PatientRead` (включает `studies`?)
   - `src/models/study.py` — `StudyRead` (включает `series`?)
   - `src/models/record.py` — `RecordRead` (включает `patient`?)

2. **Репозитории** — добавить eager loading:
   - `src/repositories/patient_repository.py`
   - `src/api/routers/study.py` (или `record.py`)

### Предлагаемое решение

Добавить `selectinload` в запросы, где response model включает вложенные связи:

```python
from sqlalchemy.orm import selectinload

# Пример для получения пациентов со studies и series
stmt = select(Patient).options(
    selectinload(Patient.studies).selectinload(Study.series)
)
result = await session.execute(stmt)
patients = result.scalars().all()
```

Или, альтернативно, использовать плоские response models без вложенных связей для endpoints, где полная иерархия не нужна.
