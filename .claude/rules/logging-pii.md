---
paths:
  - "clarinet/api/auth_config.py"
  - "clarinet/api/routers/auth.py"
  - "clarinet/utils/logger.py"
---

# Logging PII & Headers

`clarinet.utils.logger.scrub_sensitive` already redacts `password=`, `Bearer …`,
`token=`, DB URLs, etc. **It does NOT touch URL query strings, fragments, or
arbitrary headers.** Anything you log explicitly is your responsibility.

## Headers — sanitize before logging

| Header | Risk | How to handle |
|---|---|---|
| `Referer` | path may carry tokens (`/reset/<token>`), emails, password-reset codes, OAuth state — query/fragment too | keep only `scheme://netloc`, drop the rest |
| `Origin` | usually safe (no path), but truncate to a bounded size | `[:512]` |
| `User-Agent` | safe, but unbounded — flood risk | `[:512]` |
| `Authorization` | **never log** — already scrubbed by `scrub_sensitive`, but don't put it in `extra={}` (extra is not scrubbed) |
| `Cookie` / `Set-Cookie` | **never log** — session tokens |
| `X-Forwarded-For` | IP chain, OK to log truncated |

### Referer sanitization pattern

```python
from urllib.parse import urlsplit

raw_referer = request.headers.get("Referer", "")
if raw_referer:
    parts = urlsplit(raw_referer)
    safe_referer = (
        f"{parts.scheme}://{parts.netloc}"
        if parts.scheme and parts.netloc
        else ""
    )[:512]
else:
    safe_referer = ""
```

Path-only referers (no scheme/netloc) collapse to an empty string —
the path itself is never preserved because it can carry secrets.

Reference: `clarinet/api/auth_config.py:on_after_login`.

## loguru `extra=` quirk

Loguru is **not** stdlib `logging`. When you pass `extra={"foo": "bar"}` to
`logger.info(...)`, loguru does not splat keys onto the record — it nests them
under `record["extra"]["extra"]`:

```python
logger.info("msg", extra={"reason": "x"})
# record["extra"] == {"extra": {"reason": "x"}}
```

This is the project convention throughout `auth_config.py`. Tests that inspect
records must reach for `record["extra"]["extra"]["reason"]`, not
`record["extra"]["reason"]`.

### Two ways to attach context

| Pattern | When | Pitfall |
|---|---|---|
| `logger.info("msg", extra={...})` | one-off log call, project convention | nested under `extra["extra"]` |
| `logger.bind(**ctx).info("msg")` | reusable context, propagates to children | requires returning/threading the bound logger |

Stick with `extra=` to match existing code unless you have a reason to switch.

## What `_LokiSink` actually ships

`clarinet/utils/logger.py:_LokiSink` only forwards `t/l/mod/fn/line/msg` plus
`exc` — **not the `extra` dict**. So `extra={...}` payloads:

- Survive on the loguru record (visible to `caplog`-style sinks and to JSON
  file logs via `_json_format`)
- Are **not** delivered to remote Loki

If you add a new structured field that should land in remote logs, extend
`_LokiSink.__call__` with an explicit allowlist of pre-sanitized keys taken
from `record["extra"]["extra"]` (mind the nesting). Do **not** forward the
nested dict wholesale — that would push every future header/token preview a
caller attaches into remote logs.

## jq filtering

Token validation branches in `read_token` use `extra={"reason": "..."}` so that
`/tmp/clarinet.log` (when `serialize=True`) can be filtered by failure reason:

```bash
jq 'select(.extra.extra.reason == "ip_mismatch")' /tmp/clarinet.log
jq 'select(.extra.extra.reason == "not_found_or_expired")' /tmp/clarinet.log
jq 'select(.extra.extra.reason == "idle_timeout")' /tmp/clarinet.log
jq 'select(.extra.extra.reason == "user_not_found")' /tmp/clarinet.log
```

When adding a new failure branch, give it a `reason` so it joins the same
filter set — see `tests/test_auth_logging.py` for the regression suite.
