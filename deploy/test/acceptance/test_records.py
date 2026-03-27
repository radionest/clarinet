"""Acceptance tests: record type listing."""

import httpx


def test_list_record_types(auth_client: httpx.Client) -> None:
    """GET /records/types → 200 with a list."""
    response = auth_client.get("/records/types")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
