---
paths:
  - "clarinet/services/schema_hydration.py"
  - "tasks/**/hydrators.py"
---

# Schema Hydration — dynamic field options

## How it works

JSON Schema fields with `x-options` markers get resolved to `oneOf` arrays at runtime via registered hydrator callbacks. Built-in hydrators are registered at import time; project-specific ones live in `plan/hydrators.py` (loaded automatically via `load_custom_hydrators`).

## Writing a hydrator

```python
from clarinet.services.schema_hydration import HydrationContext, schema_hydrator

@schema_hydrator("source_name")
async def hydrate_source(record, options, ctx: HydrationContext) -> list[dict]:
    items = await ctx.some_repo.get_all()
    return [{"const": str(item.id), "title": item.name} for item in items]
```

## HydrationContext

`HydrationContext` is the sole data access interface for hydrators. Contains pre-built repository instances (`study_repo`, `user_repo`). Constructed via `HydrationContext.from_session()`.

Do not extract session from existing repos (`ctx.study_repo.session`). If a hydrator needs a new repository — add it to `HydrationContext` and `from_session()`.

## Known limitation: `_walk` and `items`

`_walk` calls `_hydrate_field` on each `properties` value but not on `items` nodes directly — only recurses into them. So `x-options` placed directly on `items` (not inside `items.properties`) will not be hydrated.

**Workaround:** wrap the value in an object inside `items.properties` so hydration reaches it through the `properties` path.
