"""Integration tests for RecordType CRUD API endpoints (GET/PATCH/DELETE)."""

import pytest
import pytest_asyncio
from httpx import AsyncClient

from clarinet.models.record import RecordType

# Base URL prefix for record endpoints
BASE = "/api/records"


@pytest_asyncio.fixture
async def sample_record_type(test_session) -> RecordType:
    """Create a sample RecordType in the DB for testing."""
    rt = RecordType(
        name="test-edit-type",
        description="Original description",
        label="Original Label",
        data_schema={"type": "object", "properties": {"field1": {"type": "string"}}},
        slicer_script_args={"arg1": "val1"},
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def record_type_with_parent_output(client: AsyncClient, auth_headers) -> str:
    """RecordType with unique_by={'parent'}, parent_required=True, and an OUTPUT
    pattern keyed by {parent_id} — valid under that combo, but not under the
    default {"user", "parent"} (missing {user_id})."""
    payload = {
        "name": "parent-scoped-type",
        "level": "SERIES",
        "parent_required": True,
        "unique_by": ["parent"],
        "file_registry": [
            {
                "name": "review_out",
                "pattern": "review_{parent_id}.seg.nrrd",
                "role": "output",
                "required": True,
                "multiple": False,
            }
        ],
    }
    response = await client.post(f"{BASE}/types", json=payload, headers=auth_headers)
    assert response.status_code == 201
    return payload["name"]


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
    async def test_update_edit_window_days_and_clear_with_null(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """Explicit null clears edit_window_days (the exclude_none exception)."""
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"edit_window_days": 14},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["edit_window_days"] == 14

        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"edit_window_days": None},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["edit_window_days"] is None

    @pytest.mark.asyncio
    async def test_update_explicit_null_unique_by_clears_it(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """Explicit null on unique_by disables the constraint (the exclude_none
        exception, mirroring edit_window_days). Regression: exclude_none used to
        silently drop this, so PATCH {"unique_by": null} was a no-op."""
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"unique_by": None},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["unique_by"] is None

    @pytest.mark.asyncio
    async def test_update_invalid_unique_by_token_rejected(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """An unknown unique_by partition token is rejected, not silently
        smuggled in — RecordTypeOptional has no canonicalizing validator, so
        this is caught by the merged-result RecordTypeCreate construction."""
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"unique_by": ["series"]},
            headers=auth_headers,
        )
        assert response.status_code in (400, 422)

        get_response = await client.get(
            f"{BASE}/types/{sample_record_type.name}", headers=auth_headers
        )
        assert get_response.json()["unique_by"] == ["parent", "user"]

    @pytest.mark.asyncio
    async def test_update_unique_by_combo_output_cannot_discriminate_rejected(
        self, client: AsyncClient, auth_headers, record_type_with_parent_output
    ):
        """Flipping unique_by to a combination the existing OUTPUT pattern can't
        discriminate is rejected at PATCH time — DB unchanged. In TOML mode this
        would otherwise export to disk and fail the next startup."""
        response = await client.patch(
            f"{BASE}/types/{record_type_with_parent_output}",
            json={"unique_by": ["user", "parent"]},
            headers=auth_headers,
        )
        assert response.status_code in (409, 422)

        get_response = await client.get(
            f"{BASE}/types/{record_type_with_parent_output}", headers=auth_headers
        )
        assert get_response.json()["unique_by"] == ["parent"]

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


class TestUiSchemaField:
    """Tests for the ui_schema field on RecordType (formosh presentation hints)."""

    @pytest.mark.asyncio
    async def test_get_returns_ui_schema_when_set(
        self, client: AsyncClient, auth_headers, test_session
    ):
        """GET /types/{name} should include ui_schema in the response."""
        rt = RecordType(
            name="rt-ui-get",
            data_schema={"type": "object"},
            ui_schema={"ui:order": ["a", "b"]},
        )
        test_session.add(rt)
        await test_session.commit()

        response = await client.get(f"{BASE}/types/{rt.name}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["ui_schema"] == {"ui:order": ["a", "b"]}

    @pytest.mark.asyncio
    async def test_patch_with_dict_ui_schema(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """PATCH should accept ui_schema as a dict."""
        ui = {"ui:order": ["field1"], "field1": {"ui:widget": "textarea"}}
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"ui_schema": ui},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["ui_schema"] == ui

    @pytest.mark.asyncio
    async def test_patch_with_json_string_ui_schema(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """PATCH should accept ui_schema as a JSON string (formosh textarea submission)."""
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"ui_schema": '{"ui:order": ["x"]}'},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["ui_schema"] == {"ui:order": ["x"]}

    @pytest.mark.asyncio
    async def test_patch_invalid_ui_schema_json_string(
        self, client: AsyncClient, auth_headers, sample_record_type
    ):
        """PATCH should reject malformed JSON in ui_schema with 422."""
        response = await client.patch(
            f"{BASE}/types/{sample_record_type.name}",
            json={"ui_schema": "not valid json"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_null_ui_schema_is_silently_dropped(
        self, client: AsyncClient, auth_headers, test_session
    ):
        """PATCH with ``ui_schema: null`` does NOT clear the column.

        Pre-existing service behavior (``model_dump(exclude_unset=True,
        exclude_none=True)``) mirrors ``data_schema``: explicit ``null`` is
        treated as "not set". To reset, send an empty dict ``{}`` instead.
        """
        rt = RecordType(
            name="rt-ui-null",
            data_schema={"type": "object"},
            ui_schema={"ui:order": ["a"]},
        )
        test_session.add(rt)
        await test_session.commit()

        response = await client.patch(
            f"{BASE}/types/{rt.name}",
            json={"ui_schema": None},
            headers=auth_headers,
        )
        assert response.status_code == 200
        # ui_schema remains the original value — null was silently ignored.
        assert response.json()["ui_schema"] == {"ui:order": ["a"]}


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
