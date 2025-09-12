# TODO: Переход на Session Cookies с fastapi-users

## Принципы реализации
- **KISS**: Минимальная сложность, только необходимый функционал
- **YAGNI**: Без избыточности и "на будущее"
- **Полное удаление JWT** без обратной совместимости
- **Использование fastapi-users** для всей аутентификации

## Детальный план реализации


### Этап 2: Создание моделей

#### 2.1 Создать минимальную модель пользователя
**Файл:** `src/models/user.py`

```python
"""
Simplified user model for session-based authentication.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from fastapi_users.db import SQLModelBaseUserDB
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .task import Task

# Таблица связи для ролей (остается как есть)
class UserRolesLink(SQLModel, table=True):
    """Link table for many-to-many relationship between users and roles."""
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    role_name: str = Field(foreign_key="userrole.name", primary_key=True)


# Минимальная модель пользователя
class User(SQLModelBaseUserDB, table=True):
    """
    Minimal user model extending fastapi-users base.
    
    Fields from SQLModelBaseUserDB:
    - id: UUID (автоматически)
    - email: str (уникальный)
    - hashed_password: str
    - is_active: bool = True
    - is_superuser: bool = False
    - is_verified: bool = False
    """
    # Переопределяем id как строку для совместимости с существующей БД
    id: str = Field(primary_key=True)  # username
    
    # Связи с существующими моделями
    roles: list["UserRole"] = Relationship(
        back_populates="users", 
        link_model=UserRolesLink
    )
    tasks: list["Task"] = Relationship(back_populates="user")


class UserRole(SQLModel, table=True):
    """Role model (остается без изменений)."""
    name: str = Field(primary_key=True)
    users: list[User] = Relationship(
        back_populates="roles", 
        link_model=UserRolesLink
    )
```

#### 2.2 Создать модель для хранения сессий
**Файл:** `src/models/auth.py`

```python
"""
Session storage model for cookie authentication.
"""

from datetime import datetime
from sqlmodel import Field, SQLModel


class AccessToken(SQLModel, table=True):
    """
    Database-backed session token.
    Минимальные поля согласно KISS.
    """
    token: str = Field(primary_key=True, index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # fastapi-users управляет expires_at автоматически
```

### Этап 3: Настройка fastapi-users

#### 3.1 Создать конфигурацию аутентификации
**Файл:** `src/api/auth_config.py`

```python
"""
Fastapi-users configuration for session-based authentication.
Following KISS principle - minimal configuration.
"""

import uuid
from typing import Optional

from fastapi import Request, Response
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    Strategy,
)
from fastapi_users.db import SQLModelUserDatabase
from fastapi_users_db_sqlmodel import SQLModelBaseOAuthAccount
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.auth import AccessToken
from src.models.user import User
from src.settings import settings
from src.utils.logger import logger


# Минимальный UserManager
class UserManager(UUIDIDMixin, BaseUserManager[User, str]):
    """Minimal user manager - только необходимые методы."""
    
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        logger.info(f"User {user.id} has registered.")

    async def on_after_login(
        self, user: User, request: Optional[Request] = None, response: Optional[Response] = None
    ):
        logger.info(f"User {user.id} logged in.")


async def get_user_db(session: AsyncSession):
    """Get user database."""
    yield SQLModelUserDatabase(session, User)


async def get_user_manager(user_db=Depends(get_user_db)):
    """Get user manager."""
    yield UserManager(user_db)


# Настройка Cookie транспорта (KISS - только куки, без токенов)
cookie_transport = CookieTransport(
    cookie_name=settings.cookie_name,  # "clarinet_session"
    cookie_max_age=settings.session_expire_seconds,  # 24 часа
    cookie_httponly=True,  # Защита от XSS
    cookie_secure=not settings.debug,  # HTTPS в продакшене
    cookie_samesite="lax",  # Защита от CSRF
)


# Стратегия хранения сессий в БД
class DatabaseStrategy(Strategy[User, str]):
    """Simple database strategy for session storage."""
    
    def __init__(self, database: SQLModelUserDatabase):
        self.database = database
        
    async def write_token(self, user: User) -> str:
        """Create and store session token."""
        token = str(uuid.uuid4())
        
        # Сохраняем в БД через AccessToken
        async with self.database.session as session:
            access_token = AccessToken(
                token=token,
                user_id=user.id,
            )
            session.add(access_token)
            await session.commit()
            
        return token
    
    async def read_token(self, token: Optional[str], user_manager: UserManager) -> Optional[User]:
        """Validate and read session token."""
        if not token:
            return None
            
        async with self.database.session as session:
            # Находим токен в БД
            statement = select(AccessToken).where(AccessToken.token == token)
            results = await session.execute(statement)
            access_token = results.scalar_one_or_none()
            
            if not access_token:
                return None
                
            # Получаем пользователя
            return await self.database.get(access_token.user_id)
    
    async def destroy_token(self, token: str, user: User) -> None:
        """Remove session token on logout."""
        async with self.database.session as session:
            statement = delete(AccessToken).where(AccessToken.token == token)
            await session.execute(statement)
            await session.commit()


def get_database_strategy(user_db=Depends(get_user_db)) -> DatabaseStrategy:
    """Get database strategy."""
    return DatabaseStrategy(user_db)


# Создание authentication backend
auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_database_strategy,
)

# Создание FastAPIUsers instance
fastapi_users = FastAPIUsers[User, str](
    get_user_manager,
    [auth_backend],
)

# Экспорт готовых dependencies
current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
optional_current_user = fastapi_users.current_user(optional=True)
```

### Этап 4: Обновление роутеров

#### 4.1 Заменить auth router
**Файл:** `src/api/routers/auth.py`

```python
"""
Simplified authentication router using fastapi-users.
"""

from fastapi import APIRouter

from src.api.auth_config import auth_backend, fastapi_users
from src.models.user import User

# Используем готовые роутеры от fastapi-users
router = APIRouter(prefix="/auth", tags=["auth"])

# Добавляем стандартные эндпоинты
router.include_router(
    fastapi_users.get_auth_router(auth_backend),
)

# Дополнительные эндпоинты при необходимости
@router.get("/me")
async def get_me(user: User = Depends(current_active_user)):
    """Get current user."""
    return user
```

#### 4.2 Обновить user router
**Файл:** `src/api/routers/user.py`

```python
"""
User management router using fastapi-users.
"""

from fastapi import APIRouter

from src.api.auth_config import fastapi_users
from src.models.user import User

router = APIRouter(prefix="/users", tags=["users"])

# Используем готовые CRUD роутеры
router.include_router(
    fastapi_users.get_users_router(User, User),
)

# Кастомные эндпоинты для ролей остаются
@router.get("/{user_id}/roles")
async def get_user_roles(
    user_id: str,
    current_user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    # Existing role logic
    pass
```

### Этап 5: Обновление зависимостей

#### 5.1 Заменить dependencies
**Файл:** `src/api/dependencies.py`

```python
"""
Simplified dependencies using fastapi-users.
"""

from src.api.auth_config import (
    current_active_user,
    current_superuser,
    optional_current_user,
)

# Экспортируем для использования в других модулях
get_current_user_async = current_active_user
get_current_superuser_async = current_superuser
get_optional_user_async = optional_current_user

# Для совместимости можно добавить алиасы
get_current_user = current_active_user
```

### Этап 6: Обновление настроек

#### 6.1 Очистить settings.py
**Файл:** `src/settings.py`

```python
# Удалить все JWT настройки:
# - jwt_secret_key
# - jwt_algorithm  
# - jwt_expire_minutes

# Добавить минимальные настройки для сессий:
class Settings(BaseSettings):
    # ... existing settings ...
    
    # Session settings (KISS - только необходимое)
    cookie_name: str = "clarinet_session"
    session_expire_hours: int = 24
    
    @property
    def session_expire_seconds(self) -> int:
        return self.session_expire_hours * 3600
    
    # Secret key для подписи (использовать существующий)
    secret_key: str = Field(...)  # Тот же что был для JWT
```

### Этап 7: Обновление app.py

#### 7.1 Подключить роутеры
**Файл:** `src/api/app.py`

```python
from src.api.routers import auth, user
from src.api.auth_config import fastapi_users, auth_backend

# В функции create_app:
app.include_router(auth.router)
app.include_router(user.router)

# Удалить все упоминания JWT
```

### Этап 8: Миграция базы данных

#### 8.1 Создать миграцию Alembic
```bash
alembic revision --autogenerate -m "Switch to session-based auth with fastapi-users"
```

#### 8.2 SQL миграция (упрощенная)
```sql
-- Добавить новые поля в user
ALTER TABLE user ADD COLUMN IF NOT EXISTS email VARCHAR UNIQUE;
ALTER TABLE user ADD COLUMN IF NOT EXISTS hashed_password VARCHAR;
ALTER TABLE user ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
ALTER TABLE user ADD COLUMN IF NOT EXISTS is_superuser BOOLEAN DEFAULT FALSE;
ALTER TABLE user ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;

-- Мигрировать данные
UPDATE user SET 
    email = CONCAT(id, '@local.clarinet'),  -- Временный email
    hashed_password = password,
    is_active = isactive
WHERE email IS NULL;

-- Создать таблицу сессий
CREATE TABLE IF NOT EXISTS accesstoken (
    token VARCHAR PRIMARY KEY,
    user_id VARCHAR REFERENCES user(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Удалить старые поля после проверки
ALTER TABLE user DROP COLUMN IF EXISTS password;
ALTER TABLE user DROP COLUMN IF EXISTS isactive;

-- Удалить старую таблицу сессий
DROP TABLE IF EXISTS httpsession;
```

#### 8.3 Применить миграцию
```bash
alembic upgrade head
```

### Этап 9: Удаление старого кода

#### 9.1 Файлы для полного удаления:
- `src/api/security.py` - весь файл JWT логики
- Все импорты `from src.api.security import ...`

#### 9.2 Очистить импорты:
```bash
# Найти и удалить все импорты JWT
grep -r "from src.api.security" src/
grep -r "jwt" src/
grep -r "token" src/
grep -r "OAuth2PasswordBearer" src/
```

### Этап 10: Тестирование

#### 10.1 Проверить эндпоинты
```bash
# Регистрация (если нужна)
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "password"}'

# Вход
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=user@example.com&password=password" \
  -c cookies.txt

# Проверка аутентификации
curl http://localhost:8000/auth/me -b cookies.txt

# Выход
curl -X POST http://localhost:8000/auth/logout -b cookies.txt
```

#### 10.2 Запустить тесты
```bash
pytest tests/integration/test_auth.py -v
```

### Этап 11: Очистка и финализация

#### 11.1 Проверка кода
```bash
# Форматирование
black src/ tests/
isort src/ tests/

# Линтинг
ruff check src/ --fix

# Типы
mypy src/
```

#### 11.2 Обновить тесты
- Заменить JWT моки на сессионные
- Обновить фикстуры для использования cookies
- Удалить тесты JWT токенов

## Чек-лист выполнения

### Подготовка
- [ ] Создать бэкап БД
- [ ] Создать ветку для изменений
- [ ] Установить fastapi-users

### Реализация
- [ ] Создать новые модели (User, AccessToken)
- [ ] Настроить fastapi-users конфигурацию
- [ ] Заменить authentication роутеры
- [ ] Обновить dependencies
- [ ] Очистить settings от JWT
- [ ] Обновить app.py

### Миграция
- [ ] Создать Alembic миграцию
- [ ] Применить миграцию к БД
- [ ] Проверить данные после миграции

### Очистка
- [ ] Удалить src/api/security.py
- [ ] Удалить все JWT импорты
- [ ] Удалить неиспользуемые зависимости

### Тестирование
- [ ] Проверить login/logout
- [ ] Проверить сохранение сессий в БД
- [ ] Проверить работу с cookies
- [ ] Запустить интеграционные тесты

### Финализация
- [ ] Запустить форматтеры и линтеры
- [ ] Обновить документацию API
- [ ] Создать PR с изменениями

## Возможные проблемы и решения

| Проблема | Решение |
|----------|---------|
| Конфликт с существующими user_id | Использовать username как id |
| Сессии не истекают | Добавить background task для очистки |

## Команды для быстрого старта

```bash
# Установка зависимостей
poetry add "fastapi-users[sqlmodel]"

# Создание миграции
alembic revision --autogenerate -m "Session auth with fastapi-users"

# Применение миграции
alembic upgrade head

# Запуск приложения
uvicorn src.api.app:app --reload

# Проверка кода
black src/ && isort src/ && ruff check src/ --fix && mypy src/
```

## Ссылки на документацию

- [FastAPI Users Documentation](https://fastapi-users.github.io/fastapi-users/latest/)
- [Cookie Authentication](https://fastapi-users.github.io/fastapi-users/latest/configuration/authentication/strategies/database/)
- [SQLModel Integration](https://fastapi-users.github.io/fastapi-users/latest/configuration/databases/sqlmodel/)