"""Integration tests for RecordType CRUD API endpoints (GET/PATCH/DELETE)."""

import pytest
import pytest_asyncio
from httpx import AsyncClient

from src.models.record import RecordType

# Base URL prefix for record endpoints
BASE = "/api/records"


@pytest_asyncio.fixture
async def sample_record_type(test_session) -> RecordType:
    """Create a sample RecordType in the DB for testing."""
    rt = RecordType(
        name="test_edit_type",
        description="Original description",
        label="Original Label",
        data_schema={"type": "object", "properties": {"field1": {"type": "string"}}},
        slicer_script_args={"arg1": "val1"},
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


class TestGetRecordType:
    """Tests for GET /types/{record_type_id}."""

    @pytest.mark.asyncio
    async def test_get_existing_record_type(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """Should return full record type data for an existing type."""
        response = await client.get(f"{BASE}/types/{sample_record_type.name}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == sample_record_type.name
        assert data["description"] == "Original description"
        assert data["label"] == "Original Label"
        assert data["data_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_get_nonexistent_record_type(self, client: AsyncClient, auth_headers):
        """Should return 404 for a non-existent record type."""
        response = await client.get(f"{BASE}/types/nonexistent_type_xyz", headers=auth_headers)
        assert response.status_code == 404


class TestUpdateRecordType:
    """Tests for PATCH /types/{record_type_id}."""

    @pytest.mark.asyncio
    async def test_update_description(self, client: AsyncClient, auth_headers, sample_record_type):
        """Should update a simple string field."""
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"description": "Updated description"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated description"
        # Other fields should be preserved
        assert data["name"] == sample_record_type.name
        assert data["label"] == "Original Label"

    @pytest.mark.asyncio
    async def test_update_with_json_string_data_schema(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """Should accept data_schema as a JSON string and parse it."""
        new_schema = '{"type": "object", "properties": {"new_field": {"type": "integer"}}}'
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"data_schema": new_schema},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data_schema"]["properties"]["new_field"]["type"] == "integer"

    @pytest.mark.asyncio
    async def test_update_with_json_string_slicer_args(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """Should accept slicer_script_args as a JSON string."""
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"slicer_script_args": '{"new_arg": "new_val"}'},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["slicer_script_args"] == {"new_arg": "new_val"}

    @pytest.mark.asyncio
    async def test_update_with_dict_data_schema(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """Should accept data_schema as a dict (standard behavior)."""
        new_schema = {"type": "object", "properties": {"x": {"type": "number"}}}
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"data_schema": new_schema},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data_schema"]["properties"]["x"]["type"] == "number"

    @pytest.mark.asyncio
    async def test_update_invalid_json_string(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """Should return 422 for invalid JSON string in data_schema."""
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"data_schema": "not valid json"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_invalid_data_schema(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """Should return 422 for valid JSON but invalid JSON Schema."""
        # A schema with an invalid 'type' value triggers SchemaError
        invalid_schema = {"type": "not_a_valid_type"}
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"data_schema": invalid_schema},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_nonexistent_record_type(self, client: AsyncClient, auth_headers):
        """Should return 404 when updating a non-existent record type."""
        response = await client.patch(
            f"{BASE}/types/nonexistent_type_xyz",
            json={"description": "won't work"},
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_preserves_unset_fields(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """Partial update should not clear fields that weren't sent."""
        # Update only description
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"description": "Only this changes"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Only this changes"
        # Original data_schema should still be present
        assert data["data_schema"] is not None
        assert "field1" in data["data_schema"].get("properties", {})
        # Original slicer_script_args should still be present
        assert data["slicer_script_args"] == {"arg1": "val1"}


class TestDeleteRecordType:
    """Tests for DELETE /types/{record_type_id}."""

    @pytest.mark.asyncio
    async def test_delete_record_type(self, client: AsyncClient, auth_headers, sample_record_type):
        """Should delete an existing record type and return 204."""
        response = await client.delete(
            f"{BASE}/types/{sample_record_type.name}", headers=auth_headers
        )
        assert response.status_code == 204

        # Verify it's gone
        get_response = await client.get(
            f"{BASE}/types/{sample_record_type.name}", headers=auth_headers
        )
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_record_type(self, client: AsyncClient, auth_headers):
        """Should return 404 when deleting a non-existent record type."""
        response = await client.delete(f"{BASE}/types/nonexistent_type_xyz", headers=auth_headers)
        assert response.status_code == 404
