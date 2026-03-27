"""Acceptance tests: auth cookie flow."""

import httpx


def test_login_and_me(auth_client: httpx.Client, admin_email: str) -> None:
    """Login with admin creds → cookie → GET /auth/me → 200."""
    response = auth_client.get("/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == admin_email


def test_unauthenticated_me_rejected(api_client: httpx.Client) -> None:
    """GET /auth/me without cookie → 401."""
    response = api_client.get("/auth/me")
    assert response.status_code == 401
