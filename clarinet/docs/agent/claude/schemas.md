---
paths:
  - "plan/schemas/**"
---

# Раздел `plan/schemas/`

JSON Schema-документы, описывающие форму поля `record.data` для тех типов записей, у которых есть структурированные данные. Используются и для **валидации** на бэкенде, и для **генерации UI-форм** на фронтенде.

## Именование и связь

- **Имя файла**: `{record-type-name}.schema.json` — kebab-case, совпадает с `RecordDef.name`. Пример: `first-check.schema.json`.
- **Расположение**: `plan/schemas/`.
- **Связь с RecordDef**:
  ```python
  RecordDef(
      name="first-check",
      data_schema="schemas/first-check.schema.json",  # путь относительно plan/
      ...
  )
  ```
- **Альтернатива** — inline `dict` прямо в `data_schema=...`. Используется для очень коротких схем (1-2 поля), но обычно schema-файл удобнее.

Если `data_schema` не указано, фреймворк ищет sidecar-файл `<config_tasks_path>/schemas/<record-type-name>.schema.json` автоматически.

## Общие определения между файлами (`$ref`)

Повторяющиеся под-схемы выносятся в отдельный файл и переиспользуются стандартным относительным `$ref`. При загрузке конфигурации clarinet **встраивает** внешнее определение в локальный `$defs` схемы (one-time bundling) — итоговая схема самодостаточна, поэтому и валидатор, и UI-форма видят обычный `#/$defs/...`.

Общий файл (`plan/schemas/_common.schema.json`) держит определения в блоке `$defs`:

```json
{
  "$defs": {
    "StudyType": { "type": "string", "title": "Тип исследования", "enum": ["CT", "MRI"] }
  }
}
```

Ссылка из схемы записи — относительным путём от каталога самой схемы:

```json
{
  "type": "object",
  "properties": {
    "study_type": { "$ref": "_common.schema.json#/$defs/StudyType" }
  }
}
```

**Поддерживается:** именованные определения `<файл>#/$defs/<Имя>` (или `#/definitions/<Имя>`); внутри подтягиваются sibling-ссылки `#/$defs/*` из **того же** файла.

**Не поддерживается** (даёт `ConfigLoadError` на старте): ссылка на файл целиком (`{"$ref": "_common.schema.json"}` без указателя); цепочки между файлами (общий файл сам `$ref`-ает третий); inline-`dict`-схема в Python не сканируется (там переиспользуйте композицию dict).

Конвенция: общие файлы с префиксом `_` или в подкаталоге `defs/` — чтобы отличать их от схем типов записей (`{record-type-name}.schema.json`).

## Базовая структура

```json
{
  "type": "object",
  "properties": {
    "field_a": { "type": "string" },
    "field_b": { "type": "integer", "minimum": 0 }
  },
  "required": ["field_a"]
}
```

Поддерживается полный JSON Schema (Draft 2020-12). Бэкенд использует библиотеку `jsonschema`, фронтенд — собственный form-builder.

## Conditional schemas (`if/then/else`)

Для зависимых полей: показывать/требовать одни поля только при определённом значении других.

```json
{
  "type": "object",
  "properties": {
    "is_good": { "type": "boolean" }
  },
  "required": ["is_good"],
  "if": {
    "properties": { "is_good": { "const": true } }
  },
  "then": {
    "properties": {
      "study_type": {
        "type": "string",
        "enum": ["CT", "MRI", "CT-AG"]
      },
      "best_series": { "type": "string" }
    },
    "required": ["study_type", "best_series"]
  },
  "unevaluatedProperties": false
}
```

`unevaluatedProperties: false` запрещает поля, не описанные явно в `properties` (включая `then` ветки), — защита от опечаток.

## `x-options` — UI-хинты

Кастомное расширение для подсказок form-builder-у. Игнорируется валидатором, но используется фронтендом.

```json
{
  "best_series": {
    "type": "string",
    "x-options": { "source": "study_series" }
  },
  "attendees": {
    "type": "array",
    "items": { "type": "string" },
    "x-options": { "source": "users" }
  }
}
```

| `source` | UI-эффект |
|---|---|
| `study_series` | Селект из серий текущего study |
| `users` | Селект из пользователей системы |

Список доступных источников расширяется фронтендом — смотрите репо frontend для актуального списка.

## Локализация (поле `title`)

Frontend использует `title` вместо имени поля для меток на форме. Пишите на любом языке проекта (русский — типично):

```json
{
  "lesions": {
    "type": "array",
    "title": "Очаги",
    "items": {
      "type": "object",
      "properties": {
        "lesion_num": { "type": "integer", "title": "Очаг №", "readOnly": true },
        "classification": {
          "type": "string",
          "title": "Классификация",
          "enum": ["metastasis", "cyst", "hemangioma"]
        }
      }
    }
  }
}
```

## Read-only поля

Стандартный JSON Schema атрибут `readOnly: true` — поле показывается, но не редактируется. Используется для системных значений (`lesion_num`, заполняемый при создании записи).

## Вложенные массивы объектов

Для коллекций (списки очагов, mappings, attendees):

```json
{
  "lesions": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "lesion_num": { "type": "integer", "readOnly": true },
        "cluster": { "type": "integer", "minimum": 1 }
      },
      "required": ["lesion_num"]
    }
  }
}
```

## Полный пример

```json
{
  "type": "object",
  "title": "Заключение МДК",
  "properties": {
    "lesions": {
      "type": "array",
      "title": "Очаги",
      "items": {
        "type": "object",
        "properties": {
          "lesion_num": { "type": "integer", "title": "Очаг №", "readOnly": true },
          "classification": {
            "type": "string",
            "title": "Классификация",
            "enum": ["metastasis", "unclear", "cyst", "hemangioma", "benign"]
          },
          "treatment": {
            "type": "string",
            "title": "Лечение",
            "enum": ["resection", "ablation", "observation"]
          }
        },
        "required": ["lesion_num", "classification", "treatment"]
      }
    },
    "attendees": {
      "type": "array",
      "title": "Участники МДК",
      "items": { "type": "string" },
      "x-options": { "source": "users" }
    },
    "conclusion_text": {
      "type": "string",
      "title": "Текст заключения"
    }
  },
  "required": ["lesions", "attendees"]
}
```
