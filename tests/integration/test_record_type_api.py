"""Integration tests for RecordType CRUD API endpoints (GET/PATCH/DELETE)."""

import pytest
import pytest_asyncio
from httpx import AsyncClient

from clarinet.models.file_schema import FileDefinitionRead
from clarinet.models.record import RecordType
from clarinet.repositories.file_definition_repository import FileDefinitionRepository
from clarinet.utils.file_link_sync import sync_file_links
from tests.utils.factories import make_record_type
from tests.utils.urls import RECORD_TYPES

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

    @pytest.mark.asyncio
    async def test_update_file_registry_returns_synced_files(
        self, client: AsyncClient, auth_headers, test_session
    ):
        """PATCH with a file_registry answers with the files it just synced (#567).

        Regression: after ``sync_file_links(clear_existing=True)`` the in-memory
        ``file_links`` collection was empty, and the final ``repo.get`` served
        that same identity-mapped object, so the response carried
        ``"file_registry": []`` while the DB held the correct rows.
        """
        seg_input = {
            "name": "seg_input",
            "pattern": "seg_{id}.nrrd",
            "role": "input",
            "required": True,
            "multiple": False,
        }
        mask_output = {
            "name": "mask_output",
            "pattern": "mask_{id}.nrrd",
            "role": "output",
            "required": True,
            "multiple": False,
        }
        report_output = {
            "name": "report_output",
            "pattern": "report_{id}.json",
            "role": "output",
            "required": False,
            "multiple": False,
        }
        response = await client.post(
            RECORD_TYPES,
            json={
                "name": "registry-patch-type",
                "level": "SERIES",
                "file_registry": [seg_input, mask_output],
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert {f["name"] for f in response.json()["file_registry"]} == {
            "seg_input",
            "mask_output",
        }

        # The reported case: the same registry sent back unchanged.
        response = await client.patch(
            f"{RECORD_TYPES}/registry-patch-type",
            json={"file_registry": [seg_input, mask_output]},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert {f["name"] for f in response.json()["file_registry"]} == {
            "seg_input",
            "mask_output",
        }

        # A changed registry: one file dropped, one added.
        response = await client.patch(
            f"{RECORD_TYPES}/registry-patch-type",
            json={"file_registry": [mask_output, report_output]},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert {f["name"] for f in response.json()["file_registry"]} == {
            "mask_output",
            "report_output",
        }

        # The response must match what a fresh read sees.
        test_session.expire_all()
        response = await client.get(f"{RECORD_TYPES}/registry-patch-type", headers=auth_headers)
        assert response.status_code == 200
        assert {f["name"] for f in response.json()["file_registry"]} == {
            "mask_output",
            "report_output",
        }

    @pytest.mark.asyncio
    async def test_sync_file_links_keeps_file_definition_loaded(self, test_session):
        """Links from ``sync_file_links`` carry the ``FileDefinition`` object, not only its id.

        The identity map holds ``FileDefinition`` rows only weakly, so a link that
        knows its definition by FK alone lazy-loads it on the next ``file_registry``
        read — a ``MissingGreenlet`` inside a request. A warm test session masks
        that; expunging turns the same lazy load into a deterministic
        ``DetachedInstanceError`` instead (#567 follow-up).
        """
        record_type = make_record_type(name="sync-fd-loaded")
        record_type.file_links = []
        test_session.add(record_type)
        await test_session.flush()

        await sync_file_links(
            record_type,
            [
                FileDefinitionRead(name="seg_input", pattern="seg_{id}.nrrd", role="input"),
                FileDefinitionRead(name="mask_output", pattern="mask_{id}.nrrd", role="output"),
            ],
            FileDefinitionRepository(test_session),
            test_session,
        )
        await test_session.commit()
        test_session.expunge_all()

        assert [fd.name for fd in record_type.file_registry] == ["seg_input", "mask_output"]


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


# A FileDefinition row is shared by every RecordType binding it. A file entry
# that omits row-level fields must inherit the stored ones, not reset them.
_VOLUME_FULL = {
    "name": "shared_volume",
    "pattern": "volume.nii.gz",
    "role": "input",
    "required": True,
    "multiple": False,
}
_SEG_FULL = {
    "name": "shared_seg",
    "pattern": "seg_{id}.nrrd",
    "role": "output",
    "required": True,
    "multiple": False,
    "grid_conform_to": "shared_volume",
    "on_grid_mismatch": "reject",
}
_VOLUME_PARTIAL = {"name": "shared_volume", "pattern": "volume.nii.gz", "role": "input"}
_SEG_PARTIAL = {"name": "shared_seg", "pattern": "seg_{id}.nrrd", "role": "output"}


@pytest_asyncio.fixture
async def guarded_type(client: AsyncClient, auth_headers) -> str:
    """'guarded-a' binds shared_volume and shared_seg, seg conforming to volume."""
    payload = {"name": "guarded-a", "level": "SERIES", "file_registry": [_VOLUME_FULL, _SEG_FULL]}
    response = await client.post(RECORD_TYPES, json=payload, headers=auth_headers)
    assert response.status_code == 201
    return "guarded-a"


async def _seg_entry(client: AsyncClient, auth_headers, type_name: str) -> dict:
    response = await client.get(f"{RECORD_TYPES}/{type_name}", headers=auth_headers)
    assert response.status_code == 200
    return next(f for f in response.json()["file_registry"] if f["name"] == "shared_seg")


class TestSharedFileDefinitions:
    """POST/PATCH /types with file entries naming files other types already bind."""

    @pytest.mark.asyncio
    async def test_post_partial_entry_inherits_stored_grid_declaration(
        self, client: AsyncClient, auth_headers, guarded_type
    ):
        """A second binder that omits the grid fields must neither clear them on
        the shared row nor end up without the guard itself."""
        payload = {
            "name": "guarded-b",
            "level": "SERIES",
            "file_registry": [_VOLUME_PARTIAL, _SEG_PARTIAL],
        }
        response = await client.post(RECORD_TYPES, json=payload, headers=auth_headers)
        assert response.status_code == 201

        for type_name in (guarded_type, "guarded-b"):
            seg = await _seg_entry(client, auth_headers, type_name)
            assert seg["grid_conform_to"] == "shared_volume"
            assert seg["on_grid_mismatch"] == "reject"

    @pytest.mark.asyncio
    async def test_post_binding_guarded_file_without_its_reference_rejected(
        self, client: AsyncClient, auth_headers, guarded_type
    ):
        """Binding shared_seg alone is rejected: its stored declaration references
        shared_volume, which this type does not bind."""
        payload = {"name": "guarded-c", "level": "SERIES", "file_registry": [_SEG_PARTIAL]}
        response = await client.post(RECORD_TYPES, json=payload, headers=auth_headers)
        assert response.status_code == 409
        assert "shared_volume" in response.text

        missing = await client.get(f"{RECORD_TYPES}/guarded-c", headers=auth_headers)
        assert missing.status_code == 404
        seg = await _seg_entry(client, auth_headers, guarded_type)
        assert seg["grid_conform_to"] == "shared_volume"

    @pytest.mark.asyncio
    async def test_patch_partial_entry_keeps_stored_grid_declaration(
        self, client: AsyncClient, auth_headers, guarded_type
    ):
        payload = {
            "name": "guarded-b",
            "level": "SERIES",
            "file_registry": [_VOLUME_FULL, _SEG_FULL],
        }
        created = await client.post(RECORD_TYPES, json=payload, headers=auth_headers)
        assert created.status_code == 201

        response = await client.patch(
            f"{RECORD_TYPES}/guarded-b",
            json={"file_registry": [_VOLUME_PARTIAL, _SEG_PARTIAL]},
            headers=auth_headers,
        )
        assert response.status_code == 200

        seg = await _seg_entry(client, auth_headers, guarded_type)
        assert seg["grid_conform_to"] == "shared_volume"
        assert seg["on_grid_mismatch"] == "reject"

    @pytest.mark.asyncio
    async def test_patch_dropping_reference_of_guarded_file_rejected(
        self, client: AsyncClient, auth_headers, guarded_type
    ):
        payload = {
            "name": "guarded-b",
            "level": "SERIES",
            "file_registry": [_VOLUME_FULL, _SEG_FULL],
        }
        created = await client.post(RECORD_TYPES, json=payload, headers=auth_headers)
        assert created.status_code == 201

        response = await client.patch(
            f"{RECORD_TYPES}/guarded-b",
            json={"file_registry": [_SEG_PARTIAL]},
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert "shared_volume" in response.text

        unchanged = await client.get(f"{RECORD_TYPES}/guarded-b", headers=auth_headers)
        assert {f["name"] for f in unchanged.json()["file_registry"]} == {
            "shared_volume",
            "shared_seg",
        }

    @pytest.mark.asyncio
    async def test_patch_explicit_null_reference_removes_the_whole_guard(
        self, client: AsyncClient, auth_headers, guarded_type, test_session
    ):
        """An explicit ``grid_conform_to: null`` drops the guard; the stored
        ``on_grid_mismatch`` must not be inherited alongside it, or the merged
        entry is an action without a reference and fails validation."""
        response = await client.patch(
            f"{RECORD_TYPES}/{guarded_type}",
            json={"file_registry": [_VOLUME_PARTIAL, {**_SEG_PARTIAL, "grid_conform_to": None}]},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # The shared test session caches the pre-PATCH link collection of the
        # patched type (tests/CLAUDE.md, "Identity Map Caching"); reload from DB.
        test_session.expire_all()
        seg = await _seg_entry(client, auth_headers, guarded_type)
        assert seg["grid_conform_to"] is None
        assert seg["on_grid_mismatch"] is None
