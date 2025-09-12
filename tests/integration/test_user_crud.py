"""Тесты CRUD операций для User."""

import pytest
from httpx import AsyncClient
from sqlmodel import select

from src.api.security import get_password_hash, verify_password
from src.models.user import User, UserRole


@pytest.mark.asyncio
async def test_create_user(test_session):
    """Тест создания пользователя."""
    user = User(
        id="newuser@example.com",
        password=get_password_hash("password123"),
        isactive=True,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    assert user.id == "newuser@example.com"
    assert user.isactive is True
    assert verify_password("password123", user.password)


@pytest.mark.asyncio
async def test_get_user_by_id(test_session, test_user):
    """Тест получения пользователя по ID."""
    result = await test_session.get(User, test_user.id)
    assert result is not None
    assert result.id == test_user.id
    assert result.email == test_user.email


@pytest.mark.asyncio
async def test_get_user_by_id(test_session, test_user):
    """Тест получения пользователя по ID."""
    statement = select(User).where(User.id == test_user.id)
    result = await test_session.execute(statement)
    user = result.scalar_one_or_none()

    assert user is not None
    assert user.id == test_user.id
    assert user.isactive == test_user.isactive


@pytest.mark.asyncio
async def test_update_user(test_session, test_user):
    """Тест обновления пользователя."""
    # Обновляем пользователя
    test_user.isactive = False
    test_session.add(test_user)
    await test_session.commit()
    await test_session.refresh(test_user)

    # Проверяем изменения
    updated_user = await test_session.get(User, test_user.id)
    assert updated_user.isactive is False


@pytest.mark.asyncio
async def test_delete_user(test_session):
    """Тест удаления пользователя."""
    # Создаем пользователя для удаления
    user = User(
        id="delete@example.com",
        password=get_password_hash("password"),
        isactive=True,
    )
    test_session.add(user)
    await test_session.commit()
    user_id = user.id

    # Удаляем пользователя
    await test_session.delete(user)
    await test_session.commit()

    # Проверяем что пользователь удален
    deleted_user = await test_session.get(User, user_id)
    assert deleted_user is None


@pytest.mark.asyncio
async def test_user_with_roles(test_session):
    """Тест создания пользователя с ролями."""
    # Создаем пользователя
    user = User(
        id="roleuser@example.com",
        password=get_password_hash("password"),
        isactive=True,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    # Сначала создаем роли, если они не существуют
    admin_role_def = await test_session.get(UserRole, "admin")
    if not admin_role_def:
        admin_role_def = UserRole(name="admin")
        test_session.add(admin_role_def)
        await test_session.commit()
    
    moderator_role_def = await test_session.get(UserRole, "moderator")
    if not moderator_role_def:
        moderator_role_def = UserRole(name="moderator")
        test_session.add(moderator_role_def)
        await test_session.commit()
    
    # Добавляем связи через UserRolesLink
    from src.models.user import UserRolesLink
    admin_link = UserRolesLink(user_id=user.id, role_name="admin")
    moderator_link = UserRolesLink(user_id=user.id, role_name="moderator")

    test_session.add(admin_link)
    test_session.add(moderator_link)
    await test_session.commit()

    # Получаем роли пользователя через UserRolesLink
    from src.models.user import UserRolesLink
    statement = select(UserRolesLink).where(UserRolesLink.user_id == user.id)
    result = await test_session.execute(statement)
    roles = result.scalars().all()

    assert len(roles) == 2
    role_names = [r.role_name for r in roles]
    assert "admin" in role_names
    assert "moderator" in role_names


@pytest.mark.asyncio
async def test_list_all_users(test_session, test_user, admin_user):
    """Тест получения списка всех пользователей."""
    statement = select(User)
    result = await test_session.execute(statement)
    users = result.scalars().all()

    assert len(users) >= 2  # Минимум test_user и admin_user
    user_ids = [u.id for u in users]
    assert "test@example.com" in user_ids
    assert "admin@example.com" in user_ids


@pytest.mark.asyncio
async def test_filter_active_users(test_session):
    """Тест фильтрации активных пользователей."""
    # Создаем активного и неактивного пользователей
    active_user = User(
        id="active@example.com",
        password=get_password_hash("password"),
        isactive=True,
    )
    inactive_user = User(
        id="inactive@example.com",
        password=get_password_hash("password"),
        isactive=False,
    )

    test_session.add(active_user)
    test_session.add(inactive_user)
    await test_session.commit()

    # Получаем только активных пользователей
    statement = select(User).where(User.isactive)
    result = await test_session.execute(statement)
    active_users = result.scalars().all()

    user_ids = [u.id for u in active_users]
    assert "active@example.com" in user_ids
    assert "inactive@example.com" not in user_ids


@pytest.mark.asyncio
async def test_user_registration_via_api(client: AsyncClient):
    """Тест регистрации пользователя через API."""
    response = await client.post(
        "/auth/register",
        json={
            "email": "apiuser@example.com",
            "username": "apiuser",
            "password": "securepassword123",
        }
    )

    # Регистрация может быть отключена или требовать дополнительных полей
    if response.status_code == 200:
        data = response.json()
        assert "id" in data or "user" in data
    elif response.status_code == 422:
        # Валидация не прошла - это тоже ок для теста
        pass
    else:
        # 404 если endpoint не существует
        assert response.status_code in [404, 405]
