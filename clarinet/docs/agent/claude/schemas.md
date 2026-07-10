---
paths:
  - "plan/schemas/**"
---

# The `plan/schemas/` section

JSON Schema documents describing the shape of the `record.data` field for record types that have structured data. Used both for **backend validation** and for **frontend UI-form generation**.

## Naming and linkage

- **File name**: `{record-type-name}.schema.json` — kebab-case, matching `RecordDef.name`. Example: `first-check.schema.json`.
- **Location**: `plan/schemas/`.
- **Link to RecordDef**:
  ```python
  RecordDef(
      name="first-check",
      data_schema="schemas/first-check.schema.json",  # path relative to plan/
      ...
  )
  ```
- **Alternative** — an inline `dict` directly in `data_schema=...`. Used for very short schemas (1-2 fields), but a schema file is usually more convenient.

If `data_schema` isn't specified, the framework automatically looks for the sidecar file `<config_tasks_path>/schemas/<record-type-name>.schema.json`.

## Shared definitions across files (`$ref`)

Repeated sub-schemas are extracted into a separate file and reused via a standard relative `$ref`. When clarinet loads the configuration, it **inlines** the external definition into the schema's local `$defs` (one-time bundling) — the resulting schema is self-contained, so both the validator and the UI form see a plain `#/$defs/...`.

The shared file (`plan/schemas/_common.schema.json`) keeps definitions in a `$defs` block:

```json
{
  "$defs": {
    "StudyType": { "type": "string", "title": "Study type", "enum": ["CT", "MRI"] }
  }
}
```

Referencing it from a record's schema — a relative path from the schema's own directory:

```json
{
  "type": "object",
  "properties": {
    "study_type": { "$ref": "_common.schema.json#/$defs/StudyType" }
  }
}
```

**Supported:** named definitions `<file>#/$defs/<Name>` (or `#/definitions/<Name>`); sibling references `#/$defs/*` within the **same** file are also pulled in.

**Not supported** (raises `ConfigLoadError` at startup): referencing a whole file (`{"$ref": "_common.schema.json"}` with no pointer); chains between files (a shared file itself `$ref`-ing a third file); an inline `dict` schema in Python is not scanned (reuse dict composition there instead).

Convention: prefix shared files with `_` or place them in a `defs/` subdirectory — to distinguish them from record-type schemas (`{record-type-name}.schema.json`).

## Basic structure

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

Full JSON Schema (Draft 2020-12) is supported. The backend uses the `jsonschema` library; the frontend has its own form-builder.

## Conditional schemas (`if/then/else`)

For dependent fields: show/require certain fields only when others have a specific value.

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

`unevaluatedProperties: false` forbids fields not explicitly described in `properties` (including in `then` branches) — a guard against typos.

## `x-options` — UI hints

A custom extension providing hints to the form-builder. Ignored by the validator, but used by the frontend.

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

| `source` | UI effect |
|---|---|
| `study_series` | Select from the current study's series |
| `users` | Select from system users |

The list of available sources is extended by the frontend — check the frontend repo for the current list.

A typo in `source` for a config-defined RecordType fails startup: `reconcile_config` raises `ConfigurationError`, naming the unknown source. For types mutated via the API, and for orphaned records, this check doesn't run — there a typo only surfaces as a WARNING ("Unknown x-options source") at render time, and the field is left unrendered ("raw").

## Localization (the `title` field)

The frontend uses `title` instead of the field name for form labels. Write it in whatever language your project uses (Russian is typical for this framework's clinical deployments):

```json
{
  "lesions": {
    "type": "array",
    "title": "Lesions",
    "items": {
      "type": "object",
      "properties": {
        "lesion_num": { "type": "integer", "title": "Lesion #", "readOnly": true },
        "classification": {
          "type": "string",
          "title": "Classification",
          "enum": ["metastasis", "cyst", "hemangioma"]
        }
      }
    }
  }
}
```

## Read-only fields

The standard JSON Schema attribute `readOnly: true` — the field is shown but not editable. Used for system values (`lesion_num`, populated when the record is created).

## Nested arrays of objects

For collections (lists of lesions, mappings, attendees):

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

## Full example

```json
{
  "type": "object",
  "title": "MDT conclusion",
  "properties": {
    "lesions": {
      "type": "array",
      "title": "Lesions",
      "items": {
        "type": "object",
        "properties": {
          "lesion_num": { "type": "integer", "title": "Lesion #", "readOnly": true },
          "classification": {
            "type": "string",
            "title": "Classification",
            "enum": ["metastasis", "unclear", "cyst", "hemangioma", "benign"]
          },
          "treatment": {
            "type": "string",
            "title": "Treatment",
            "enum": ["resection", "ablation", "observation"]
          }
        },
        "required": ["lesion_num", "classification", "treatment"]
      }
    },
    "attendees": {
      "type": "array",
      "title": "MDT attendees",
      "items": { "type": "string" },
      "x-options": { "source": "users" }
    },
    "conclusion_text": {
      "type": "string",
      "title": "Conclusion text"
    }
  },
  "required": ["lesions", "attendees"]
}
```
