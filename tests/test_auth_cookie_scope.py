"""Session cookie scoping for sub-path deployments (several apps on one host)."""

from http.cookies import SimpleCookie

import pytest
from fastapi import Response

from clarinet.api.auth_config import ScopedCookieTransport, session_cookie_path


def _set_cookies(response: Response) -> set[tuple[str, str, str]]:
    """(value, path, max-age) of every Set-Cookie header."""
    out: set[tuple[str, str, str]] = set()
    for header in response.headers.getlist("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        (morsel,) = jar.values()
        out.add((morsel.value, morsel["path"], str(morsel["max-age"])))
    return out


@pytest.mark.parametrize(
    ("root_url", "expected"),
    [("", "/"), ("/", "/"), ("/nir_liver", "/nir_liver"), ("/nir_liver/", "/nir_liver")],
)
def test_session_cookie_path(root_url: str, expected: str) -> None:
    assert session_cookie_path(root_url) == expected


def _transport(path: str) -> ScopedCookieTransport:
    return ScopedCookieTransport(
        cookie_name="clarinet_session", cookie_max_age=3600, cookie_path=path
    )


@pytest.mark.asyncio
async def test_login_under_sub_path_scopes_cookie_and_expires_legacy() -> None:
    response = await _transport("/nir_liver").get_login_response("tok")
    assert _set_cookies(response) == {("tok", "/nir_liver", "3600"), ("", "/", "0")}


@pytest.mark.asyncio
async def test_logout_under_sub_path_expires_both_cookies() -> None:
    response = await _transport("/nir_liver").get_logout_response()
    assert _set_cookies(response) == {("", "/nir_liver", "0"), ("", "/", "0")}


@pytest.mark.asyncio
async def test_login_at_root_does_not_delete_own_cookie() -> None:
    response = await _transport("/").get_login_response("tok")
    assert _set_cookies(response) == {("tok", "/", "3600")}
