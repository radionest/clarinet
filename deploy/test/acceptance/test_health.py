"""Acceptance tests: health endpoint."""

import httpx


def test_health_ok(api_client: httpx.Client) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"
