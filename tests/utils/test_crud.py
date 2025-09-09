"""
Tests for the CRUD utilities in src/utils/crud.py.

This module tests the Create, Read, Update, Delete operations
and JSON validation utilities using both mocks and a real SQLite database.
"""

import pytest
from typing import Dict, Any, Optional, Generator
from unittest.mock import MagicMock, patch
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, SQLModel, Session, create_engine, SQLModel
from sqlalchemy.pool import StaticPool
from jsonschema import ValidationError, SchemaError

from src.utils.crud import (
    add_item,
    get_item,
    update_item,
    delete_item,
    validate_json_by_schema,
    get_sql_type_from_value,
)
from src.exceptions import CONFLICT, NOT_FOUND


# Define test models
class TestModel(SQLModel, table=True):
    """Test model for CRUD operations."""
    
    id: int = Field(primary_key=True)
    name: str
    description: Optional[str] = None


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    session = MagicMock(spec=Session)
    return session


@pytest.fixture
def test_item():
    """Create a test item."""
    return TestModel(id=1, name="Test Item", description="Test Description")


@pytest.fixture
def test_schema():
    """Create a test JSON schema."""
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "value": {"type": "number"},
            "tags": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["name", "value"]
    }


class TestAddItem:
    """Tests for the add_item function."""
    
    def test_add_item_success(self, mock_session, test_item):
        """Test successful item addition."""
        # Act
        result = add_item(test_item, mock_session)
        
        # Assert
        mock_session.add.assert_called_once_with(test_item)
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once_with(test_item)
        assert result == test_item
    
    def test_add_item_integrity_error(self, mock_session, test_item):
        """Test handling of IntegrityError."""
        # Arrange
        mock_session.commit.side_effect = IntegrityError(
            statement="statement", 
            params={}, 
            orig=Exception("Duplicate key")
        )
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            add_item(test_item, mock_session)
        
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
        mock_session.rollback.assert_called_once()


class TestGetItem:
    """Tests for the get_item function."""
    
    def test_get_item_success(self, mock_session, test_item):
        """Test successful item retrieval."""
        # Arrange
        mock_session.get.return_value = test_item
        
        # Act
        result = get_item(TestModel, 1, mock_session)
        
        # Assert
        mock_session.get.assert_called_once_with(TestModel, 1)
        assert result == test_item
    
    def test_get_item_not_found(self, mock_session):
        """Test item not found scenario."""
        # Arrange
        mock_session.get.return_value = None
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            get_item(TestModel, 999, mock_session)
        
        assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in excinfo.value.detail


class TestUpdateItem:
    """Tests for the update_item function."""
    
    def test_update_item_success(self, mock_session, test_item):
        """Test successful item update."""
        # Arrange
        update_data = {"name": "Updated Name", "description": "Updated Description"}
        
        # Act
        result = update_item(test_item, update_data, mock_session)
        
        # Assertd
        assert test_item.name == "Updated Name"
        assert test_item.description == "Updated Description"
        mock_session.add.assert_called_once_with(test_item)
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once_with(test_item)
        assert result == test_item
    
    def test_update_item_integrity_error(self, mock_session, test_item):
        """Test handling of IntegrityError during update."""
        # Arrange
        update_data = {"name": "Invalid Name"}
        mock_session.commit.side_effect = IntegrityError(
            statement="statement", 
            params={}, 
            orig=Exception("Constraint violation")
        )
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            update_item(test_item, update_data, mock_session)
        
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
        mock_session.rollback.assert_called_once()


class TestDeleteItem:
    """Tests for the delete_item function."""
    
    def test_delete_item_success(self, mock_session, test_item):
        """Test successful item deletion."""
        # Act
        delete_item(test_item, mock_session)
        
        # Assert
        mock_session.delete.assert_called_once_with(test_item)
        mock_session.commit.assert_called_once()
    
    def test_delete_item_integrity_error(self, mock_session, test_item):
        """Test handling of IntegrityError during deletion."""
        # Arrange
        mock_session.commit.side_effect = IntegrityError(
            statement="statement", 
            params={}, 
            orig=Exception("Foreign key constraint")
        )
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            delete_item(test_item, mock_session)
        
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
        mock_session.rollback.assert_called_once()


class TestValidateJsonBySchema:
    """Tests for the validate_json_by_schema function."""
    
    def test_validate_json_success(self, test_schema):
        """Test successful JSON validation."""
        # Arrange
        valid_data = {"name": "Test", "value": 42, "tags": ["tag1", "tag2"]}
        
        # Act
        result = validate_json_by_schema(valid_data, test_schema)
        
        # Assert
        assert result is True
    
    def test_validate_json_validation_error(self, test_schema):
        """Test handling of ValidationError."""
        # Arrange
        invalid_data = {"name": "Test", "value": "not a number"}  # Value should be a number
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            validate_json_by_schema(invalid_data, test_schema)
        
        assert excinfo.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "validation failed" in excinfo.value.detail
    
    def test_validate_json_missing_required(self, test_schema):
        """Test validation with missing required field."""
        # Arrange
        invalid_data = {"name": "Test"}  # Missing required 'value' field
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            validate_json_by_schema(invalid_data, test_schema)
        
        assert excinfo.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_validate_json_schema_error(self):
        """Test handling of SchemaError."""
        # Arrange
        invalid_schema = {"type": "invalid-type"}  # Invalid schema type
        data = {"name": "Test"}
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            validate_json_by_schema(data, invalid_schema)
        
        assert excinfo.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid JSON schema" in excinfo.value.detail


class TestGetSqlTypeFromValue:
    """Tests for the get_sql_type_from_value function."""
    
    def test_get_string_type(self):
        """Test getting SQL type for string values."""
        from sqlalchemy import String
        
        # Act
        result = get_sql_type_from_value("test")
        
        # Assert
        assert result == String
    
    def test_get_boolean_type(self):
        """Test getting SQL type for boolean values."""
        from sqlalchemy import Boolean
        
        # Act
        result = get_sql_type_from_value(True)
        
        # Assert
        assert result == Boolean
    
    def test_get_integer_type(self):
        """Test getting SQL type for integer values."""
        from sqlalchemy import Integer
        
        # Act
        result = get_sql_type_from_value(42)
        
        # Assert
        assert result == Integer
    
    def test_get_float_type(self):
        """Test getting SQL type for float values."""
        from sqlalchemy import Float
        
        # Act
        result = get_sql_type_from_value(3.14)
        
        # Assert
        assert result == Float
    
    def test_get_none_type(self):
        """Test getting SQL type for None values."""
        from sqlalchemy import String
        
        # Act
        result = get_sql_type_from_value(None)
        
        # Assert
        assert result == String
    
    def test_get_unsupported_type(self):
        """Test handling of unsupported types."""
        # Arrange
        complex_value = complex(1, 2)
        
        # Act & Assert
        with pytest.raises(NotImplementedError):
            get_sql_type_from_value(complex_value)


# Integration tests with mock database
@pytest.mark.parametrize(
    "operation,expected_exception,exception_status",
    [
        ("add", CONFLICT, status.HTTP_409_CONFLICT),
        ("get", NOT_FOUND, status.HTTP_404_NOT_FOUND),
        ("update", CONFLICT, status.HTTP_409_CONFLICT),
        ("delete", CONFLICT, status.HTTP_409_CONFLICT),
    ]
)
def test_exception_with_context(
    operation, expected_exception, exception_status, mock_session, test_item
):
    """Test that exceptions use the with_context method correctly."""
    # Arrange
    context_message = "Custom error message"
    
    if operation == "add":
        mock_session.commit.side_effect = IntegrityError(
            statement="statement", params={}, orig=Exception("Duplicate key")
        )
        test_function = lambda: add_item(test_item, mock_session)
    
    elif operation == "get":
        mock_session.get.return_value = None
        test_function = lambda: get_item(TestModel, 999, mock_session)
    
    elif operation == "update":
        mock_session.commit.side_effect = IntegrityError(
            statement="statement", params={}, orig=Exception("Constraint violation")
        )
        test_function = lambda: update_item(test_item, {"name": "New Name"}, mock_session)
    
    elif operation == "delete":
        mock_session.commit.side_effect = IntegrityError(
            statement="statement", params={}, orig=Exception("Foreign key constraint")
        )
        test_function = lambda: delete_item(test_item, mock_session)
    
    # Mock the with_context method to return a new exception with the context message
    with patch.object(
        expected_exception.__class__, 
        'with_context',
        return_value=HTTPException(status_code=exception_status, detail=context_message)
    ) as mock_with_context:
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            test_function()
        
        # Check that with_context was called
        mock_with_context.assert_called()
        assert excinfo.value.status_code == exception_status


# ------------------------------------------------------------------------------
# Integration tests with real SQLite database
# ------------------------------------------------------------------------------

# Define a model with a unique constraint for testing
class UniqueTestModel(SQLModel, table=True):
    """Test model with a unique constraint."""
    
    id: int = Field(primary_key=True)
    name: str = Field(unique=True)
    description: Optional[str] = None

# Define a model with a foreign key constraint for testing
class ParentModel(SQLModel, table=True):
    """Parent model for foreign key constraint testing."""
    
    id: int = Field(primary_key=True)
    name: str


class ChildModel(SQLModel, table=True):
    """Child model with foreign key constraint."""
    
    id: int = Field(primary_key=True)
    name: str
    parent_id: int = Field(foreign_key="parentmodel.id")


@pytest.fixture
def sqlite_engine():
    """Create an in-memory SQLite database engine."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
def sqlite_session(sqlite_engine) -> Generator[Session, None, None]:
    """Create a database session for the in-memory SQLite database."""
    with Session(sqlite_engine) as session:
        yield session


class TestCrudWithSQLite:
    """Integration tests using a real SQLite database."""
    
    def test_add_item_success(self, sqlite_session):
        """Test adding an item to the database."""
        # Arrange
        test_item = TestModel(id=1, name="Test Item", description="Test Description")
        
        # Act
        result = add_item(test_item, sqlite_session)
        
        # Assert
        assert result.id == 1
        assert result.name == "Test Item"
        assert result.description == "Test Description"
        
        # Verify item is in the database
        db_item = sqlite_session.get(TestModel, 1)
        assert db_item is not None
        assert db_item.name == "Test Item"
    
    def test_add_item_duplicate_primary_key(self, sqlite_session):
        """Test adding an item with a duplicate primary key."""
        # Arrange
        sqlite_session.add(TestModel(id=1, name="Original Item"))
        sqlite_session.commit()
        
        duplicate_item = TestModel(id=1, name="Duplicate Item")
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            add_item(duplicate_item, sqlite_session)
        
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
    
    def test_add_item_unique_constraint_violation(self, sqlite_session):
        """Test adding an item that violates a unique constraint."""
        # Arrange
        sqlite_session.add(UniqueTestModel(id=1, name="Unique Name"))
        sqlite_session.commit()
        
        duplicate_name_item = UniqueTestModel(id=2, name="Unique Name")
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            add_item(duplicate_name_item, sqlite_session)
        
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
    
    def test_get_item_success(self, sqlite_session):
        """Test getting an item from the database."""
        # Arrange
        test_item = TestModel(id=1, name="Test Item", description="Test Description")
        sqlite_session.add(test_item)
        sqlite_session.commit()
        
        # Act
        result = get_item(TestModel, 1, sqlite_session)
        
        # Assert
        assert result.id == 1
        assert result.name == "Test Item"
        assert result.description == "Test Description"
    
    def test_get_item_not_found(self, sqlite_session):
        """Test getting a non-existent item."""
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            get_item(TestModel, 999, sqlite_session)
        
        assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in excinfo.value.detail
    
    def test_update_item_success(self, sqlite_session):
        """Test updating an item in the database."""
        # Arrange
        test_item = TestModel(id=1, name="Original Name", description="Original Description")
        sqlite_session.add(test_item)
        sqlite_session.commit()
        
        update_data = {"name": "Updated Name", "description": "Updated Description"}
        
        # Act
        result = update_item(test_item, update_data, sqlite_session)
        
        # Assert
        assert result.id == 1
        assert result.name == "Updated Name"
        assert result.description == "Updated Description"
        
        # Verify changes are in the database
        db_item = sqlite_session.get(TestModel, 1)
        assert db_item.name == "Updated Name"
        assert db_item.description == "Updated Description"
    
    def test_update_item_unique_constraint_violation(self, sqlite_session):
        """Test updating an item that would violate a unique constraint."""
        # Arrange
        sqlite_session.add(UniqueTestModel(id=1, name="First Item"))
        sqlite_session.add(UniqueTestModel(id=2, name="Second Item"))
        sqlite_session.commit()
        
        item_to_update = sqlite_session.get(UniqueTestModel, 2)
        update_data = {"name": "First Item"}  # This will violate the unique constraint
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            update_item(item_to_update, update_data, sqlite_session)
        
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
    
    def test_delete_item_success(self, sqlite_session):
        """Test deleting an item from the database."""
        # Arrange
        test_item = TestModel(id=1, name="Test Item")
        sqlite_session.add(test_item)
        sqlite_session.commit()
        
        # Act
        delete_item(test_item, sqlite_session)
        
        # Assert
        db_item = sqlite_session.get(TestModel, 1)
        assert db_item is None
    
    def test_delete_item_foreign_key_constraint(self, sqlite_session):
        """Test deleting an item that would violate a foreign key constraint."""
        # Arrange
        parent = ParentModel(id=1, name="Parent")
        sqlite_session.add(parent)
        sqlite_session.commit()
        
        child = ChildModel(id=1, name="Child", parent_id=1)
        sqlite_session.add(child)
        sqlite_session.commit()
        
        # Act & Assert
        with pytest.raises(HTTPException) as excinfo:
            delete_item(parent, sqlite_session)
        
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
        
        # Verify parent still exists
        db_parent = sqlite_session.get(ParentModel, 1)
        assert db_parent is not None
    
    def test_complex_crud_scenario(self, sqlite_session):
        """Test a complex CRUD scenario with multiple operations."""
        # Add multiple items
        items = [
            TestModel(id=1, name="Item 1", description="First item"),
            TestModel(id=2, name="Item 2", description="Second item"),
            TestModel(id=3, name="Item 3", description="Third item"),
        ]
        
        for item in items:
            add_item(item, sqlite_session)
        
        # Verify all items were added
        all_items = sqlite_session.exec(
            "SELECT * FROM testmodel ORDER BY id"
        ).all()
        assert len(all_items) == 3
        
        # Update an item
        item_to_update = get_item(TestModel, 2, sqlite_session)
        update_item(item_to_update, {"name": "Updated Item 2"}, sqlite_session)
        
        # Verify update worked
        updated_item = get_item(TestModel, 2, sqlite_session)
        assert updated_item.name == "Updated Item 2"
        
        # Delete an item
        item_to_delete = get_item(TestModel, 3, sqlite_session)
        delete_item(item_to_delete, sqlite_session)
        
        # Verify deletion worked
        with pytest.raises(HTTPException) as excinfo:
            get_item(TestModel, 3, sqlite_session)
        assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND
        
        # Verify remaining items
        remaining_items = sqlite_session.exec(
            "SELECT * FROM testmodel ORDER BY id"
        ).all()
        assert len(remaining_items) == 2
        assert [item.id for item in remaining_items] == [1, 2]