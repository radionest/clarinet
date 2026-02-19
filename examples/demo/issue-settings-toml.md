# settings.toml не подхватывается из рабочей директории

## Проблема 1: settings.toml не загружается

### Описание

При запуске сервера из `examples/demo/` файл `settings.toml` не читается классом `Settings` (pydantic-settings). Все настройки остаются дефолтными, несмотря на то что CWD корректный.

### Воспроизведение

```bash
cd examples/demo
uv run python -c "from src.settings import settings; print(settings.debug, settings.admin_password)"
# Вывод: False None
# Ожидалось: True admin123
```

При этом `os.getcwd()` возвращает правильный путь `examples/demo/`.

### Конфигурация

`src/settings.py`:
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=["settings.toml", "settings.custom.toml"],
        env_prefix="CLARINET_",
        extra="ignore"
    )
    debug: bool = False
    admin_password: str | None = None
    # ...
```

`examples/demo/settings.toml`:
```toml
debug = true
admin_password = "admin123"
database_driver = "sqlite"
database_name = "clarinet_demo"
# ...
```

### Гипотеза

`pydantic_settings` с `SettingsConfigDict(toml_file=...)` может резолвить путь к TOML-файлу не от CWD, а от расположения модуля `settings.py` (т.е. от `src/settings.py`). В таком случае он ищет `src/settings.toml`, которого нет.

### Текущий workaround

Передача всех настроек через переменные окружения:

```bash
CLARINET_DEBUG=true \
CLARINET_ADMIN_PASSWORD=admin123 \
CLARINET_DATABASE_DRIVER=sqlite \
CLARINET_DATABASE_NAME=clarinet_demo \
uv run python -m uvicorn src.api.app:app
```

### Последствия

Без корректной загрузки `settings.toml`:
- `debug = False` -> cookie ставится с флагом `Secure` (проблема 2)
- `admin_password = None` -> сервер падает при создании admin-пользователя
- `database_name` остаётся дефолтным -> БД создаётся не там где ожидалось
- `recordflow_enabled = False` -> RecordFlow не запускается

---

## Проблема 2: Cookie с флагом Secure на HTTP

### Описание

Когда `settings.debug = False` (из-за проблемы 1), cookie-транспорт настраивается как:

```python
# src/api/auth_config.py:78
cookie_secure=not settings.debug  # True когда debug=False
```

Браузеры и HTTP-клиенты (httpx) не отправляют cookie с флагом `Secure` по незащищённому HTTP-соединению. В результате:

1. `POST /api/auth/login` -> 204 No Content + `Set-Cookie: clarinet_session=...; Secure`
2. `GET /api/auth/me` -> 401 Unauthorized (cookie не отправлена клиентом)

### Воспроизведение

```bash
# Логин проходит:
curl -v -X POST http://localhost:8000/api/auth/login \
  -d "username=admin@clarinet.ru&password=admin123"
# Set-Cookie: clarinet_session=...; HttpOnly; Secure

# Но сессия не работает:
curl -b "clarinet_session=..." http://localhost:8000/api/auth/me
# 401 Unauthorized
```

### Решение

Решается вместе с проблемой 1 — если `settings.toml` загрузится корректно и `debug=true`, то `cookie_secure=False`.

Альтернативно можно добавить в `settings.toml`:
```toml
session_secure_cookie = false
```
и использовать выделенную настройку вместо привязки к `debug`:

```python
cookie_secure=settings.session_secure_cookie
```
