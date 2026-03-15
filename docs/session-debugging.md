# Session Authentication Debugging Guide

Этот документ описывает, как собирать и анализировать логи для диагностики проблем с авторизацией, особенно при работе с Slicer.

## Проблема

Иногда после операций Slicer (submit, validate) пользователь неожиданно теряет авторизацию и получает редирект на `/login` с сообщением "Session expired. Please log in again."

## Включение детального логирования

### Backend

1. Создайте или отредактируйте `settings.toml`:

```toml
log_level = "DEBUG"
log_serialize = true  # JSON логи для удобного парсинга

# Отключить idle timeout для исключения этой гипотезы
session_idle_timeout_minutes = 0

# Отключить IP validation
session_ip_check = false

# Увеличить TTL кэша (опционально)
session_cache_ttl_seconds = 300
```

2. Перезапустите приложение:

```bash
make run-api
```

### Frontend

Логи автоматически выводятся в консоль браузера (DevTools → Console).

## Сбор логов

### 1. Воспроизведите проблему

1. Залогиньтесь в систему
2. Откройте запись в Slicer или выполните валидацию
3. Дождитесь завершения операции (или ошибки авторизации)

### 2. Соберите логи

#### Backend логи

Логи находятся в `clarinet.log` (по умолчанию).

Фильтрация по ключевым событиям:

```bash
# Все ошибки авторизации
jq 'select(.l == "WARNING" or .l == "ERROR") | select(.msg | contains("Token") or contains("Session"))' clarinet.log

# Конкретная сессия (замените TOKEN_PREFIX на первые 8 символов токена)
jq 'select(.token_preview == "TOKEN_PREFIX")' clarinet.log

# Slicer операции
jq 'select(.operation | contains("slicer"))' clarinet.log

# Временной диапазон (последние 10 минут)
jq 'select(.t > "'$(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S)'"Z")' clarinet.log
```

#### Frontend логи (консоль браузера)

Ищите строки с префиксом `>>>`:

- `>>> Starting Slicer open: record_id=...` — начало операции открытия
- `>>> Slicer open completed successfully` — успешное завершение
- `>>> AuthError detected - msg: ...` — ошибка авторизации (ключевая строка!)

## Анализ логов

### Гипотеза 1: Сессия истекает во время длительной операции

**Признаки:**
- Backend лог: `Token validation failed` с `reason: "not_found_or_expired"`
- Frontend лог: `AuthError` сразу после завершения Slicer операции
- Время между `Starting Slicer open` и `AuthError` > 50 минут (если `session_expire_hours = 1`)

**Пример:**
```json
{
  "t": "2024-03-15T10:00:00Z",
  "l": "INFO",
  "msg": "Starting Slicer open operation: record_id=123",
  "operation": "slicer_open",
  "timeout": 60.0
}
{
  "t": "2024-03-15T10:55:00Z",
  "l": "WARNING",
  "msg": "Token validation failed: token=abcdef12...",
  "reason": "not_found_or_expired"
}
```

**Решение:** Увеличить `session_expire_hours` или добавить explicit refresh перед Slicer операцией.

---

### Гипотеза 2: Idle timeout срабатывает во время операции

**Признаки:**
- Backend лог: `Session idle timeout` с детальными метриками
- `idle_duration_seconds` > `max_idle_seconds`

**Пример:**
```json
{
  "t": "2024-03-15T10:05:00Z",
  "l": "WARNING",
  "msg": "Session idle timeout: token=abcdef12..., idle_duration=610.5s, max=600.0s",
  "reason": "idle_timeout",
  "last_accessed": "2024-03-15T09:55:00Z"
}
```

**Решение:** Отключить idle timeout (`session_idle_timeout_minutes = 0`) или обновлять `last_accessed` перед длительными операциями.

---

### Гипотеза 3: Cleanup service удаляет активную сессию

**Признаки:**
- Backend лог: `Deleting expired session` или `Deleting ancient session` с токеном, который соответствует активной сессии пользователя
- Время удаления совпадает с моментом потери авторизации

**Пример:**
```json
{
  "t": "2024-03-15T10:00:00Z",
  "l": "WARNING",
  "msg": "Deleting ancient session: token=abcdef12..., age=91 days",
  "reason": "ancient"
}
```

**Решение:** Увеличить `session_cleanup_retention_days` или исправить логику cleanup (не удалять недавно используемые сессии).

---

### Гипотеза 4: Concurrent session limit сбрасывает текущую сессию

**Признаки:**
- Backend лог: `Removed X old sessions for user` в момент создания новой сессии
- Пользователь логинился с другого устройства/браузера

**Пример:**
```json
{
  "t": "2024-03-15T10:00:00Z",
  "l": "INFO",
  "msg": "Removed 1 old sessions for user abc-123-def (limit: 5)"
}
```

**Решение:** Увеличить `session_concurrent_limit` или отключить (`session_concurrent_limit = 0`).

---

### Гипотеза 5: TTL кэш возвращает устаревшие данные

**Признаки:**
- Backend лог: `Token validated from cache` → затем следующий запрос дает `Token validation failed`
- Между событиями < `session_cache_ttl_seconds` (по умолчанию 300 секунд)

**Пример:**
```json
{
  "t": "2024-03-15T10:00:00Z",
  "l": "DEBUG",
  "msg": "Token abcdef12... validated from cache",
  "cache_hit": true
}
{
  "t": "2024-03-15T10:01:00Z",
  "l": "WARNING",
  "msg": "Token validation failed: token=abcdef12...",
  "reason": "not_found_or_expired"
}
```

**Решение:** Уменьшить `session_cache_ttl_seconds` или отключить кэш для Slicer endpoint.

---

## Временное решение

Если проблема критична, временно отключите проблемные механизмы:

```toml
# settings.toml
session_idle_timeout_minutes = 0       # Отключить idle timeout
session_ip_check = false                # Отключить IP validation
session_concurrent_limit = 0            # Отключить лимит сессий
session_expire_hours = 48               # Увеличить срок жизни сессии
session_cache_ttl_seconds = 0           # Отключить кэш (может снизить производительность!)
```

## Отчёт о баге

После сбора логов создайте отчёт с:

1. **Шаги для воспроизведения**
2. **Ожидаемое поведение**
3. **Фактическое поведение**
4. **Логи backend** (отфильтрованные по токену)
5. **Логи frontend** (консоль браузера)
6. **Конфигурация** (`settings.toml` — только session-секция)

## Инструменты для анализа

### Поиск по логам

```bash
# Все события для конкретного пользователя
jq 'select(.user_id == "YOUR-USER-UUID")' clarinet.log

# Все события для конкретной сессии
jq 'select(.token_preview == "FIRST-8-CHARS")' clarinet.log | less

# Хронология событий (отсортировать по времени)
jq -s 'sort_by(.t)' clarinet.log | less

# Экспорт в CSV для анализа в Excel/Google Sheets
jq -r '[.t, .l, .msg, .reason // "", .token_preview // ""] | @csv' clarinet.log > session_events.csv
```

### Мониторинг в реальном времени

```bash
# Следить за логами в реальном времени
tail -f clarinet.log | jq 'select(.msg | contains("Session") or contains("Token") or contains("slicer"))'
```

## Контрольный список диагностики

- [ ] Включено DEBUG логирование
- [ ] Отключен idle timeout (для исключения этой гипотезы)
- [ ] Отключена IP validation (для исключения этой гипотезы)
- [ ] Воспроизведена проблема
- [ ] Собраны логи backend (JSON)
- [ ] Собраны логи frontend (консоль браузера)
- [ ] Определён токен сессии (первые 8 символов)
- [ ] Найдены записи с `reason` в логах
- [ ] Проверена корреляция времени между Slicer операцией и AuthError
- [ ] Проверено наличие записей о cleanup в момент проблемы
- [ ] Проверено наличие записей о concurrent session limit
