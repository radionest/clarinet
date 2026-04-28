"""Acceptance tests: internal service token (X-Internal-Token) auth.

These tests act as a worker process that derives the same token from the
shared admin_password and validate the cross-process auth contract — the
deployed API derives the token the same way (PBKDF2-HMAC-SHA256, fixed
salt, fixed iteration count) so two independently configured processes
sharing only the password agree on the token.
"""

import hashlib

import httpx

# Must match clarinet.settings._derive_service_token — duplicated here so the
# test fails loudly if the algorithm changes on either side.
_SERVICE_TOKEN_SALT = b"clarinet-internal-service"
_PBKDF2_ITERATIONS = 200_000


def _derive_token(password: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        _SERVICE_TOKEN_SALT,
        iterations=_PBKDF2_ITERATIONS,
    ).hex()


def test_service_token_happy_path(
    api_client: httpx.Client,
    admin_password: str,
    admin_email: str,
) -> None:
    """Token derived from the shared admin_password authenticates as admin."""
    token = _derive_token(admin_password)
    response = api_client.get("/auth/me", headers={"X-Internal-Token": token})
    assert response.status_code == 200, (
        f"Worker-style auth rejected: {response.status_code} {response.text}"
    )
    assert response.json()["email"] == admin_email


def test_service_token_wrong_password_rejected(api_client: httpx.Client) -> None:
    """Token derived from a different admin_password is rejected with 401."""
    token = _derive_token("clarinet-acceptance-not-the-admin-password")
    response = api_client.get("/auth/me", headers={"X-Internal-Token": token})
    assert response.status_code == 401


def test_service_token_garbage_value_rejected(api_client: httpx.Client) -> None:
    """Random hex of the correct length is rejected with 401."""
    response = api_client.get("/auth/me", headers={"X-Internal-Token": "0" * 64})
    assert response.status_code == 401
