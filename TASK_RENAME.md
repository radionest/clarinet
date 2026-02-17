# План рефакторинга: Task → Record, TaskDesign → RecordType

## Проблема

Текущий нейминг `Task` и `TaskDesign` вводит в заблуждение:

- **Task** фактически является **записью данных** с нечеткой JSON-структурой, а не задачей в классическом понимании
- **TaskDesign** описывает **тип записи** с JSON-схемой валидации, а не дизайн задачи

### Что на самом деле делают эти модели:

**Task (должен быть Record):**
- Хранит JSON-данные (`result: TaskResult`) с произвольной структурой
- Привязан к медицинским сущностям (Patient, Study, Series)
- Содержит метаданные: статус, timestamps, user_id
- `result` — это фактически **данные записи**, а не результат выполнения

**TaskDesign (должен быть RecordType):**
- Определяет **тип записи** через name (PK)
- Содержит `result_schema` — JSON Schema для валидации данных
- Описывает параметры обработки (slicer_script, validators)
- Определяет ограничения доступа (role_name, max_users, min_users)
- Определяет уровень DICOM (PATIENT/STUDY/SERIES)

## Новая модель данных

```python

class SlicerSettings:
    workspace_setup_script: str | None
    workspace_setup_script_args: dict | None
    slicer_result_validator: str | None
    slicer_result_validator_args: dict | None



# RecordType - тип записи (аналог TaskDesign)
class RecordType:
    name: str                          # PK, название типа записи
    description: str | None
    label: str | None

    # JSON Schema для валидации данных
    data_schema: RecordSchema | None   # было: result_schema

    # Параметры обработки
    slicer_settings: SlicerSettings | None

    # Ограничения доступа
    role_name: str | None              # FK → UserRole
    max_users: int | None
    min_users: int | None
    level: DicomQueryLevel             # PATIENT/STUDY/SERIES

    # Relationships
    records: list["Record"]            # было: tasks
    constraint_role: UserRole | None

type RecordContextInfo = dict[str, str | int | float | RecordContextInfo ] 

# Record - запись данных (аналог Task)
class Record:
    id: int                            # PK

    # Тип записи
    record_type_name: str              # FK → RecordType.name (было: task_design_id)
    record_type: RecordType            # было: task_design

    # Данные записи (JSON с валидацией по RecordType.data_schema)
    data: RecordData | None            # было: result

    # Статус обработки
    status: RecordStatus               # было: TaskStatus
    context_info: RecordContextInfo | None           # было: info

    # Связи с медицинскими сущностями
    patient_id: str                    # FK → Patient
    patient: Patient
    study_uid: str | None              # FK → Study
    study: Study
    series_uid: str | None             # FK → Series
    series: Series | None

    # Пользователь и хранение
    user_id: UUID | None               # FK → User
    user: User | None

    # Timestamps
    created_at: datetime
    changed_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
```
