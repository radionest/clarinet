# Рефакторинг проекта Clarinet

## 📋 Сводка найденных проблем

Дата анализа: 2025-09-09  
Всего категорий проблем: 11  
Критических: 5  
Важных: 15  
Рекомендаций: 8  

## 🔴 Критические проблемы (требуют немедленного исправления)

### 1. Безопасность

#### 1.1 JWT секретный ключ в коде
**Файл:** `src/settings.py:124`
```python
# Текущий код (НЕБЕЗОПАСНО):
jwt_secret_key: str = "insecure-change-this-key-in-production"

# Исправление:
jwt_secret_key: str = os.getenv("JWT_SECRET_KEY")
if not jwt_secret_key:
    raise ValueError("JWT_SECRET_KEY must be set in environment variables")
```

#### 1.2 Небезопасная конфигурация CORS
**Файл:** `src/api/app.py:74`
```python
# Текущий код (НЕБЕЗОПАСНО):
origins = ["http://localhost", "http://localhost:8080", "*"]

# Исправление:
origins = settings.allowed_origins  # Из переменных окружения
# Убрать "*" полностью
```

#### 1.3 Небезопасные куки
**Файл:** `src/api/routers/auth.py:52`
```python
# Текущий код:
secure=not settings.debug,

# Исправление:
secure=settings.is_production,  # Отдельный флаг для production
httponly=True,
samesite="strict"
```

### 2. Синтаксические ошибки

#### 2.1 Неверный синтаксис вызова функции
**Файл:** `examples/ex1/data.py:36`
```python
# Текущий код (ОШИБКА):
result = await self.handle_task(msg: Message)

# Исправление:
result = await self.handle_task(msg)
```

#### 2.2 Неопределенная функция kick
**Файл:** `src/services/pipeline/core.py:49`
```python
# Текущий код (ОШИБКА):
map(lambda s: kick(s.step), steps)

# Исправление:
# Определить функцию или удалить строку
async def kick(step):
    """Запускает шаг pipeline"""
    return await step.execute()
```

#### 2.3 Отсутствующие импорты
**Файл:** `examples/ex1/data.py`
```python
# Добавить в начало файла:
from typing import Optional
from models import Study, Series
```

## 🟡 Важные проблемы

### 3. Архитектура

#### 3.1 Создать сервисный слой
```bash
src/
├── services/
│   ├── __init__.py
│   ├── auth.py        # Перенести authenticate_user сюда
│   ├── user.py        # Бизнес-логика пользователей
│   └── pipeline/      # Существующий pipeline
```

**Файл:** `src/services/auth.py` (создать новый)
```python
from typing import Optional
from sqlmodel import Session
from models.user import User
from utils.security import verify_password

async def authenticate_user(
    username: str, 
    password: str, 
    session: Session
) -> Optional[User]:
    """Аутентификация пользователя"""
    user = session.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user
```

#### 3.2 Асинхронная работа с БД
**Файл:** `src/utils/database.py`
```python
# Текущий код (синхронный):
from sqlmodel import create_engine, Session

# Исправление (асинхронный):
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Создание асинхронного движка
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=20,
    max_overflow=0
)

# Асинхронная фабрика сессий
async_session = sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

# Dependency для FastAPI
async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
```

### 4. Типизация

#### 4.1 Исправить типизацию в pipeline
**Файл:** `src/services/pipeline/core.py`
```python
from typing import Callable, TypeVar, Optional, List, Any
from typing_extensions import Self

T = TypeVar('T')

class Stage:
    handler: Callable[[T], T]  # Добавить параметры типа
    
    def run(self, msg: T) -> T:  # Добавить типы
        return self.handler(msg)
    
    def handle_msg(self, msg: T) -> T:  # Убрать Any
        return self.run(msg)

class Pipeline:
    def __init__(self) -> None:
        self.stages: List[Stage[T]] = []
    
    def add_stage(self, stage: Stage[T]) -> Self:  # Импортировать Self
        self.stages.append(stage)
        return self
```

#### 4.2 Заменить Any на TypeVar
**Файл:** `src/models/base.py:19`
```python
# Текущий код:
type T = Any

# Исправление:
from typing import TypeVar
T = TypeVar('T')
```

### 5. Асинхронный код

#### 5.1 Сделать authenticate_user асинхронной
**Файл:** `src/api/routers/auth.py:30`
```python
# Текущий код:
async def login_for_access_token(...):
    user = authenticate_user(...)  # Синхронная

# Исправление:
async def login_for_access_token(...):
    user = await authenticate_user(...)  # Асинхронная
```

### 6. Обработка ошибок

#### 6.1 Добавить обработку исключений
**Файл:** `src/api/routers/user.py:135`
```python
# Текущий код:
user = get_user(username, session=session)

# Исправление:
try:
    user = get_user(username, session=session)
except UserNotFoundError as e:
    logger.error(f"Пользователь {username} не найден: {e}")
    raise HTTPException(status_code=404, detail="User not found")
except DatabaseError as e:
    logger.error(f"Ошибка БД при получении пользователя: {e}")
    raise HTTPException(status_code=500, detail="Database error")
```

#### 6.2 Разделить большие try-except блоки
**Файл:** `src/api/routers/user.py:84-91`
```python
# Текущий код (слишком много в try):
try:
    user = get_user(username)
    validate_user(user)
    update_user(user)
    send_notification(user)
except Exception as e:
    ...

# Исправление (разделить):
try:
    user = get_user(username)
except UserNotFoundError:
    ...

try:
    validate_user(user)
except ValidationError:
    ...

# И так далее для каждой операции
```

### 7. Логирование

#### 7.1 Добавить логирование аутентификации
**Файл:** `src/api/routers/auth.py`
```python
from loguru import logger

async def login_for_access_token(...):
    logger.info(f"Попытка входа пользователя: {form_data.username}")
    user = await authenticate_user(...)
    
    if not user:
        logger.warning(f"Неудачная попытка входа: {form_data.username}")
        raise HTTPException(...)
    
    logger.info(f"Успешный вход пользователя: {user.username}")
    ...

async def logout(...):
    logger.info(f"Выход пользователя: {current_user.username}")
    ...
```

## 🟢 Рекомендации по улучшению

### 8. Производительность

#### 8.1 Добавить индексы в модели
**Файл:** `src/models/user.py`
```python
class User(SQLModel, table=True):
    __table_args__ = (
        Index("ix_user_username", "username"),
        Index("ix_user_email", "email"),
    )
    
    id: int = Field(primary_key=True)
    username: str = Field(unique=True, index=True)
    email: str = Field(unique=True, index=True)
```

#### 8.2 Использовать eager loading
**Файл:** `src/api/routers/user.py:122`
```python
# Текущий код (N+1 запросы):
return user.roles

# Исправление:
from sqlalchemy.orm import selectinload

query = select(User).options(selectinload(User.roles))
user = session.exec(query).first()
return user.roles
```

### 9. Тестирование

#### 9.1 Структура тестов
```bash
tests/
├── __init__.py
├── conftest.py          # Fixtures
├── unit/
│   ├── test_auth.py
│   ├── test_pipeline.py
│   └── test_models.py
├── integration/
│   ├── test_api_auth.py
│   ├── test_api_user.py
│   └── test_database.py
└── e2e/
    └── test_full_flow.py
```

#### 9.2 Пример теста для API
**Файл:** `tests/integration/test_api_auth.py`
```python
import pytest
from httpx import AsyncClient
from main import app

@pytest.mark.asyncio
async def test_login_success():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post(
            "/auth/token",
            data={"username": "testuser", "password": "testpass"}
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

@pytest.mark.asyncio
async def test_login_invalid_credentials():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post(
            "/auth/token",
            data={"username": "testuser", "password": "wrongpass"}
        )
        assert response.status_code == 401
```

### 10. Конфигурация

#### 10.1 Разделить настройки
**Файл:** `src/config/` (создать директорию)
```python
# src/config/database.py
class DatabaseSettings(BaseSettings):
    database_url: str
    pool_size: int = 20
    echo_sql: bool = False

# src/config/security.py
class SecuritySettings(BaseSettings):
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

# src/config/app.py
class AppSettings(BaseSettings):
    app_name: str = "Clarinet"
    version: str = "0.1.0"
    debug: bool = False
    allowed_origins: List[str]
```

### 11. Дополнительные улучшения

#### 11.1 Rate limiting
**Файл:** `src/api/middleware.py` (создать)
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"]
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

#### 11.2 Health checks
**Файл:** `src/api/routers/health.py` (создать)
```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["health"])

@router.get("/health")
async def health_check():
    return {"status": "healthy"}

@router.get("/ready")
async def readiness_check(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute("SELECT 1")
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        return {"status": "not ready", "database": "disconnected", "error": str(e)}
```

## 📊 Приоритеты исправления

### Фаза 1 (Критические - 1-2 дня)
- [ ] Исправить JWT ключ в settings.py
- [ ] Убрать `*` из CORS
- [ ] Исправить синтаксические ошибки в pipeline/core.py
- [ ] Добавить отсутствующие импорты

### Фаза 2 (Важные - 3-5 дней)
- [ ] Создать сервисный слой
- [ ] Перейти на асинхронную БД
- [ ] Добавить полную типизацию
- [ ] Настроить логирование

### Фаза 3 (Улучшения - 1 неделя)
- [ ] Написать тесты (покрытие >80%)
- [ ] Добавить rate limiting
- [ ] Реорганизовать конфигурацию
- [ ] Добавить health checks
- [ ] Оптимизировать производительность

## 🛠 Инструменты для автоматизации

### Проверка кода
```bash
# Линтеры
ruff check .
mypy src/

# Форматирование
black src/
isort src/

# Безопасность
bandit -r src/
safety check

# Тесты
pytest --cov=src --cov-report=html
```

### Pre-commit hooks
**Файл:** `.pre-commit-config.yaml`
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.0
    hooks:
      - id: ruff
      - id: ruff-format
  
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.5.1
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
```

## 📝 Заметки

- Все изменения должны соответствовать CLAUDE.md
- Приоритет на безопасности и стабильности
- Тестировать каждое изменение перед деплоем
- Вести changelog для всех изменений