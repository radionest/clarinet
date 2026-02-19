# План: File Schema для RecordType

## Цель

Добавить поддержку описания файлов в RecordType с возможностью валидации и генерации имён файлов по паттернам с динамическими плейсхолдерами.

## Требования

- `input_files` и `output_files` — отдельные поля в RecordType
- Паттерн: regex + плейсхолдеры `{record.field}`
- Операции: генерация имени файла, валидация существования
- Ссылки только на текущий Record

---

## Часть 1: Модель FileDefinition

**Новый файл**: `src/models/file_schema.py`

```python
from sqlmodel import SQLModel

class FileDefinition(SQLModel):
    """Определение файла для RecordType."""

    name: str                           # Уникальный идентификатор
    pattern: str                        # Regex + плейсхолдеры {record.*}
    description: str | None = None
    required: bool = True
```
Расширение файла включается в `pattern` (например `"seg_{record.id}\\.nrrd"`).

---

## Часть 2: Обновление моделей

**Файл**: `src/models/record.py`

### RecordType — добавить в `RecordTypeBase`:

```python
input_files: list[FileDefinition] = Field(default_factory=list, sa_column=Column(JSON))
output_files: list[FileDefinition] = Field(default_factory=list, sa_column=Column(JSON))
```

### Record — добавить поле для хранения найденных файлов:

```python
files: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
```

**Семантика `Record.files`:**
- Ключ — `name` из `FileDefinition`
- Значение — имя файла (str)

---

## Часть 3: Синтаксис паттернов

### Формат

Простая строка с плейсхолдерами:

```
статическая_часть{поле_record}статическая_часть.расширение
```

### Плейсхолдеры
Это поля Record

| Плейсхолдер | Значение |
|-------------|----------|
| `{id}` | ID записи |
| `{user_id}` | UUID пользователя |
| `{patient_id}` | ID пациента |
| `{study_uid}` | Study UID |
| `{series_uid}` | Series UID |
| `{data.FIELD}` | Поле из record.data (только первый уровень) |
| `{record_type.FIELD}` | Поле из record.record_type (только первый уровень) |


### Примеры

```python
# Статическое имя
"master_model.nrrd"

# Динамическое с ID записи
"result_{id}.json"

# Динамическое с полем из data
"birads_{data.BIRADS_R}.txt"

# Комбинация плейсхолдеров
"seg_{study_uid}_{id}.seg.nrrd"
```

---

## Часть 4: Утилиты обработки паттернов

**Новый файл**: `src/utils/file_patterns.py`

> **Принцип KISS**: Только функции, без класса.

```python
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.record import Record

PLACEHOLDER_REGEX = re.compile(r"\{([^}]+)\}")


def resolve_record_field(record: "Record", field_path: str) -> str:
    """Получить значение поля по пути.

    Поддерживает: id, user_id, patient_id, study_uid, series_uid, data.FIELD, record_type.FIELD
    """
    from functools import reduce

    record_attrs = field_path.split('.')
    value = reduce(
        lambda obj, attr: getattr(obj, attr) if obj else None,
        record_attrs,
        record
    )
    return str(value) if value is not None else ""


def resolve_pattern(pattern: str, record: "Record") -> str:
    """Заменить плейсхолдеры {field} на значения из записи.

    Примеры:
        "result_{id}.json" -> "result_42.json"
        "birads_{data.BIRADS_R}.txt" -> "birads_4.txt"
    """
    def replacer(match: re.Match) -> str:
        field_path = match.group(1)
        return resolve_record_field(record, field_path)

    return PLACEHOLDER_REGEX.sub(replacer, pattern)


def match_filename(filename: str, pattern: str, record: "Record") -> bool:
    """Проверить соответствие файла паттерну (точное совпадение)."""
    expected = resolve_pattern(pattern, record)
    return filename == expected


def find_matching_file(
    directory: Path,
    pattern: str,
    record: "Record",
) -> str | None:
    """Найти файл в директории, соответствующий паттерну.

    Returns:
        Имя файла или None если не найден.
    """
    if not directory.exists():
        return None

    expected_name = resolve_pattern(pattern, record)
    expected_path = directory / expected_name

    if expected_path.is_file():
        return expected_name

    return None


def generate_filename(pattern: str, record: "Record") -> str:
    """Сгенерировать имя файла по паттерну.

    Заменяет плейсхолдеры на значения из record.
    """
    return resolve_pattern(pattern, record)
```

---

## Часть 5: Сервис валидации файлов

**Новый файл**: `src/services/file_validation.py`

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.utils.file_patterns import find_matching_file

if TYPE_CHECKING:
    from src.models.file_schema import FileDefinition
    from src.models.record import Record, RecordType


@dataclass
class FileValidationError:
    file_name: str
    error_type: str  # "missing", "pattern_mismatch"
    message: str


@dataclass
class FileValidationResult:
    valid: bool
    errors: list[FileValidationError] = field(default_factory=list)
    matched_files: dict[str, str] = field(default_factory=dict)


class FileValidator:
    """Валидатор файлов для Record."""

    def __init__(self, record_type: "RecordType"):
        self.record_type = record_type

    def validate_files(
        self,
        record: "Record",
        file_definitions: list["FileDefinition"] | None,
        directory: Path,
    ) -> FileValidationResult:
        """Валидация файлов по списку определений."""
        if not file_definitions:
            return FileValidationResult(valid=True)

        errors: list[FileValidationError] = []
        matched: dict[str, str] = {}

        for file_def in file_definitions:
            filename = find_matching_file(directory, file_def.pattern, record)

            if filename:
                matched[file_def.name] = filename
            elif file_def.required:
                errors.append(FileValidationError(
                    file_name=file_def.name,
                    error_type="missing",
                    message=f"Required file '{file_def.name}' not found (pattern: {file_def.pattern})"
                ))

        return FileValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            matched_files=matched
        )

    def validate_input_files(self, record: "Record", directory: Path) -> FileValidationResult:
        """Валидация входных файлов."""
        return self.validate_files(record, self.record_type.input_files, directory)

    def validate_output_files(self, record: "Record", directory: Path) -> FileValidationResult:
        """Валидация выходных файлов."""
        return self.validate_files(record, self.record_type.output_files, directory)
```

---

## Часть 6: Исключения

**Файл**: `src/exceptions/domain.py`

```python
class FileSchemaError(ClarinetError):
    """Базовое исключение для файловых схем."""

class FilePatternError(FileSchemaError):
    """Невалидный паттерн файла."""

class RequiredFileMissingError(FileSchemaError):
    """Обязательный файл не найден."""
```

---

## Часть 7: Интеграция с API

**Файл**: `src/api/routers/record.py`

После JSON Schema валидации:

```python
from src.services.file_validation import FileValidator

# Валидация файлов (если определены)
if record.record_type.input_files:
    validator = FileValidator(record.record_type)
    directory = Path(record.working_folder) if record.working_folder else None

    if directory:
        result = validator.validate_input_files(record, directory)
        if not result.valid:
            raise HTTPException(422, detail=[e.__dict__ for e in result.errors])

        # Сохранить найденные файлы
        record.files = result.matched_files
```

---

## Пример использования

### Создание RecordType

```json
POST /records/types
{
    "name": "ct_segmentation",
    "description": "CT сегментация",
    "level": "SERIES",
    "data_schema": {
        "type": "object",
        "properties": {
            "BIRADS_R": {"type": "integer"}
        }
    },
    "input_files": [
        {
            "name": "ct_scan",
            "pattern": "ct_scan.nrrd",
            "required": true
        }
    ],
    "output_files": [
        {
            "name": "segmentation",
            "pattern": "seg_{id}.seg.nrrd",
            "required": true
        }
    ]
}
```

### Результат валидации

**Record.data** (данные пользователя):
```json
{
    "BIRADS_R": 4
}
```

**Record.files** (найденные файлы):
```json
{
    "ct_scan": "ct_scan.nrrd",
    "segmentation": "seg_42.seg.nrrd"
}
```

---

## Порядок реализации

1. **Модель** — `file_schema.py`
2. **Обновление record.py** — добавить поля
3. **Утилиты** — `file_patterns.py`
4. **Сервис валидации** — `file_validation.py`
5. **Исключения** — обновление `domain.py`
6. **API** — интеграция в роутер
7. **Тесты** — unit тесты для утилит и валидатора

---

## Критические файлы

| Файл | Действие |
|------|----------|
| `src/models/file_schema.py` | Создать |
| `src/models/record.py` | Добавить input_files, output_files, files |
| `src/utils/file_patterns.py` | Создать |
| `src/services/file_validation.py` | Создать |
| `src/exceptions/domain.py` | Добавить исключения |
| `src/api/routers/record.py` | Интеграция |

---
