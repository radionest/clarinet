"""
Tests for the async CRUD utilities in src/utils/async_crud.py.

This module tests the asynchronous Create, Read, Update, Delete operations
using AsyncSession with an in-memory SQLite database.
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, SQLModel, text

from src.utils.async_crud import (
    add_item_async,
    count_items_async,
    delete_item_async,
    exists_async,
    get_item_async,
    get_items_async,
    get_or_create_async,
    update_item_async,
)


# Define test models
class TestModelAsync(SQLModel, table=True):
    """Test model for async CRUD operations."""
    __tablename__ = "testmodel_async"

    id: int = Field(primary_key=True)
    name: str
    description: str | None = None
    value: int = 0


class UniqueTestModelAsync(SQLModel, table=True):
    """Test model with a unique constraint."""

    id: int = Field(primary_key=True)
    name: str = Field(unique=True)
    description: str | None = None


class ParentModel(SQLModel, table=True):
    """Parent model for foreign key constraint testing."""
    __tablename__ = "parentmodel_async"

    id: int = Field(primary_key=True)
    name: str


class ChildModel(SQLModel, table=True):
    """Child model with foreign key constraint."""
    __tablename__ = "childmodel_async"

    id: int = Field(primary_key=True)
    name: str
    parent_id: int = Field(foreign_key="parentmodel_async.id")


@pytest_asyncio.fixture
async def async_engine():
    """Create an in-memory async SQLite database engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create an async database session for testing."""
    async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        # Enable foreign key constraints for SQLite
        await session.execute(text("PRAGMA foreign_keys=ON"))
        yield session


@pytest.mark.asyncio
class TestAddItemAsync:
    """Tests for the add_item_async function."""

    async def test_add_item_success(self, async_session):
        """Test successful async item addition."""
        # Arrange
        test_item = TestModelAsync(id=1, name="Test Item", description="Test Description")

        # Act
        result = await add_item_async(test_item, async_session)

        # Assert
        assert result.id == 1
        assert result.name == "Test Item"
        assert result.description == "Test Description"

        # Verify item is in the database
        db_item = await async_session.get(TestModelAsync, 1)
        assert db_item is not None
        assert db_item.name == "Test Item"

    async def test_add_item_duplicate_primary_key(self, async_session):
        """Test adding an item with a duplicate primary key."""
        # Arrange
        item1 = TestModelAsync(id=1, name="Original Item")
        await add_item_async(item1, async_session)

        duplicate_item = TestModelAsync(id=1, name="Duplicate Item")

        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            await add_item_async(duplicate_item, async_session)

        assert excinfo.value.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
class TestGetItemAsync:
    """Tests for the get_item_async function."""

    async def test_get_item_success(self, async_session):
        """Test successful async item retrieval."""
        # Arrange
        test_item = TestModelAsync(id=1, name="Test Item", description="Test Description")
        async_session.add(test_item)
        await async_session.commit()

        # Act
        result = await get_item_async(TestModelAsync, 1, async_session)

        # Assert
        assert result.id == 1
        assert result.name == "Test Item"
        assert result.description == "Test Description"

    async def test_get_item_not_found(self, async_session):
        """Test async item not found scenario."""
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            await get_item_async(TestModelAsync, 999, async_session)

        assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in excinfo.value.detail


@pytest.mark.asyncio
class TestGetItemsAsync:
    """Tests for the get_items_async function."""

    async def test_get_items_with_pagination(self, async_session):
        """Test getting multiple items with pagination."""
        # Arrange
        items = [
            TestModelAsync(id=i, name=f"Item {i}", value=i * 10)
            for i in range(1, 11)
        ]
        for item in items:
            async_session.add(item)
        await async_session.commit()

        # Act - Get first 5 items
        result = await get_items_async(TestModelAsync, async_session, skip=0, limit=5)

        # Assert
        assert len(result) == 5
        assert result[0].id == 1
        assert result[4].id == 5

        # Act - Get next 5 items
        result = await get_items_async(TestModelAsync, async_session, skip=5, limit=5)

        # Assert
        assert len(result) == 5
        assert result[0].id == 6
        assert result[4].id == 10

    async def test_get_items_with_filters(self, async_session):
        """Test getting items with filter conditions."""
        # Arrange
        items = [
            TestModelAsync(id=1, name="Apple", value=100),
            TestModelAsync(id=2, name="Banana", value=200),
            TestModelAsync(id=3, name="Apple", value=150),
        ]
        for item in items:
            async_session.add(item)
        await async_session.commit()

        # Act
        result = await get_items_async(TestModelAsync, async_session, name="Apple")

        # Assert
        assert len(result) == 2
        assert all(item.name == "Apple" for item in result)


@pytest.mark.asyncio
class TestUpdateItemAsync:
    """Tests for the update_item_async function."""

    async def test_update_item_success(self, async_session):
        """Test successful async item update."""
        # Arrange
        test_item = TestModelAsync(id=1, name="Original Name", description="Original")
        async_session.add(test_item)
        await async_session.commit()

        update_data = {"name": "Updated Name", "description": "Updated"}

        # Act
        result = await update_item_async(test_item, update_data, async_session)

        # Assert
        assert result.id == 1
        assert result.name == "Updated Name"
        assert result.description == "Updated"

        # Verify changes are in the database
        db_item = await async_session.get(TestModelAsync, 1)
        assert db_item.name == "Updated Name"

    async def test_update_item_unique_constraint_violation(self, async_session):
        """Test updating an item that would violate a unique constraint."""
        # Arrange
        item1 = UniqueTestModelAsync(id=1, name="First Item")
        item2 = UniqueTestModelAsync(id=2, name="Second Item")
        async_session.add(item1)
        async_session.add(item2)
        await async_session.commit()

        update_data = {"name": "First Item"}  # This will violate unique constraint

        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            await update_item_async(item2, update_data, async_session)

        assert excinfo.value.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
class TestDeleteItemAsync:
    """Tests for the delete_item_async function."""

    async def test_delete_item_success(self, async_session):
        """Test successful async item deletion."""
        # Arrange
        test_item = TestModelAsync(id=1, name="Test Item")
        async_session.add(test_item)
        await async_session.commit()

        # Act
        await delete_item_async(test_item, async_session)

        # Assert
        db_item = await async_session.get(TestModelAsync, 1)
        assert db_item is None

    async def test_delete_item_foreign_key_constraint(self, async_session):
        """Test deleting an item that would violate a foreign key constraint."""
        # Arrange
        parent = ParentModel(id=1, name="Parent")
        async_session.add(parent)
        await async_session.commit()

        child = ChildModel(id=1, name="Child", parent_id=1)
        async_session.add(child)
        await async_session.commit()

        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            await delete_item_async(parent, async_session)

        assert excinfo.value.status_code == status.HTTP_409_CONFLICT

        # Verify parent still exists
        db_parent = await async_session.get(ParentModel, 1)
        assert db_parent is not None


@pytest.mark.asyncio
class TestExistsAsync:
    """Tests for the exists_async function."""

    async def test_exists_true(self, async_session):
        """Test when item exists."""
        # Arrange
        test_item = TestModelAsync(id=1, name="Test Item")
        async_session.add(test_item)
        await async_session.commit()

        # Act
        result = await exists_async(TestModelAsync, async_session, name="Test Item")

        # Assert
        assert result is True

    async def test_exists_false(self, async_session):
        """Test when item doesn't exist."""
        # Act
        result = await exists_async(TestModelAsync, async_session, name="Non-existent")

        # Assert
        assert result is False


@pytest.mark.asyncio
class TestCountItemsAsync:
    """Tests for the count_items_async function."""

    async def test_count_all_items(self, async_session):
        """Test counting all items."""
        # Arrange
        items = [TestModelAsync(id=i, name=f"Item {i}") for i in range(1, 6)]
        for item in items:
            async_session.add(item)
        await async_session.commit()

        # Act
        count = await count_items_async(TestModelAsync, async_session)

        # Assert
        assert count == 5

    async def test_count_with_filters(self, async_session):
        """Test counting with filters."""
        # Arrange
        items = [
            TestModelAsync(id=1, name="Apple", value=100),
            TestModelAsync(id=2, name="Banana", value=200),
            TestModelAsync(id=3, name="Apple", value=150),
        ]
        for item in items:
            async_session.add(item)
        await async_session.commit()

        # Act
        count = await count_items_async(TestModelAsync, async_session, name="Apple")

        # Assert
        assert count == 2


@pytest.mark.asyncio
class TestGetOrCreateAsync:
    """Tests for the get_or_create_async function."""

    async def test_create_new_item(self, async_session):
        """Test creating a new item when it doesn't exist."""
        # Act
        item, created = await get_or_create_async(
            TestModelAsync,
            async_session,
            defaults={"description": "Created item"},
            id=1,
            name="New Item"
        )

        # Assert
        assert created is True
        assert item.id == 1
        assert item.name == "New Item"
        assert item.description == "Created item"

    async def test_get_existing_item(self, async_session):
        """Test getting an existing item."""
        # Arrange
        existing_item = TestModelAsync(id=1, name="Existing Item", description="Original")
        async_session.add(existing_item)
        await async_session.commit()

        # Act
        item, created = await get_or_create_async(
            TestModelAsync,
            async_session,
            defaults={"description": "Should not be used"},
            id=1
        )

        # Assert
        assert created is False
        assert item.id == 1
        assert item.name == "Existing Item"
        assert item.description == "Original"


@pytest.mark.asyncio
class TestComplexAsyncScenario:
    """Test complex async CRUD scenarios."""

    async def test_complex_crud_workflow(self, async_session):
        """Test a complex async CRUD workflow with multiple operations."""
        # Add multiple items
        items = [
            TestModelAsync(id=i, name=f"Item {i}", value=i * 10)
            for i in range(1, 6)
        ]

        for item in items:
            await add_item_async(item, async_session)

        # Verify all items were added
        all_items = await get_items_async(TestModelAsync, async_session)
        assert len(all_items) == 5

        # Update an item
        item_to_update = await get_item_async(TestModelAsync, 3, async_session)
        await update_item_async(
            item_to_update,
            {"name": "Updated Item 3", "value": 999},
            async_session
        )

        # Verify update worked
        updated_item = await get_item_async(TestModelAsync, 3, async_session)
        assert updated_item.name == "Updated Item 3"
        assert updated_item.value == 999

        # Delete an item
        item_to_delete = await get_item_async(TestModelAsync, 5, async_session)
        await delete_item_async(item_to_delete, async_session)

        # Verify deletion worked
        with pytest.raises(HTTPException):
            await get_item_async(TestModelAsync, 5, async_session)

        # Verify remaining items
        remaining_items = await get_items_async(TestModelAsync, async_session)
        assert len(remaining_items) == 4
        assert all(item.id != 5 for item in remaining_items)

        # Test exists and count
        assert await exists_async(TestModelAsync, async_session, id=1) is True
        assert await exists_async(TestModelAsync, async_session, id=5) is False
        assert await count_items_async(TestModelAsync, async_session) == 4
