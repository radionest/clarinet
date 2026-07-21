---
paths:
  - "clarinet/services/schema_hydration.py"
  - "tasks/**/schema_hydrators.py"
  - "plan/**/schema_hydrators.py"
---

# Schema Hydration — dynamic field options

Deep reference: [Project configuration and the clarinet_plan package](../../docs/kb/plan-package.md) (registry loading, fail-fast).

## How it works

JSON Schema fields with `x-options` markers get resolved to `oneOf` arrays at runtime via registered hydrator callbacks. Built-in hydrators are registered at import time (`_register_builtin_hydrators`); project-specific ones live in `plan/schema_hydrators.py` (the `config_schema_hydrators_file` default), loaded automatically via `load_custom_hydrators` as the `clarinet_plan.schema_hydrators` submodule (see `.claude/rules/custom-code-loading.md`).

## Reconcile-time validation

For config-defined RecordTypes, `x-options.source` names in `data_schema` are validated at startup: `reconcile_config` fail-fasts with `ConfigurationError` on any source missing from the hydrator registry (built-ins + `schema_hydrators.py`, loaded before reconcile). Sources are collected by `collect_x_options_sources` — a sync mirror of `_walk` that only inspects the positions the runtime hydrates (each `properties` value). Boundary: types mutated via the API (TOML mode) and orphaned DB rows keep relying on the runtime WARNING in `_hydrate_field`.

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

## Known limitations: `items` and `$ref`/`$defs`

`_walk` calls `_hydrate_field` on each `properties` value but not on `items` nodes directly — only recurses into them. So `x-options` placed directly on `items` (not inside `items.properties`) will not be hydrated.

**Workaround:** wrap the value in an object inside `items.properties` so hydration reaches it through the `properties` path.

`_walk` also does not resolve `$ref` or descend into `$defs`, so an `x-options` inside a `$ref`-ed sub-schema is neither hydrated at runtime nor validated at reconcile. `collect_x_options_sources` mirrors this exactly (no false startup failures) — keep `x-options` on inline `properties` reachable without a `$ref` hop.
