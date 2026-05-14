"""Shared httpx.AsyncClient cookie-forwarding patch.

httpx.AsyncClient sets cookies in its jar but does NOT automatically include
them as a ``Cookie`` header on subsequent requests when the request is issued
through a custom transport (notably ASGITransport in test setups). This helper
monkey-patches ``client.request`` to forward the jar's cookies on every call,
so session cookies set by ``/api/auth/login`` are propagated to follow-up
requests.

Used by every fixture that exercises real cookie-based auth.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


def patch_cookie_forwarding(client: AsyncClient) -> AsyncClient:
    """Monkey-patch ``client.request`` to forward jar cookies as a header.

    Returns the same client for fluent chaining.
    """
    original_request = client.request

    async def request_with_cookies(method: str, url: str, **kwargs: Any) -> Any:
        if client.cookies:
            cookie_header = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
            if cookie_header:
                headers = kwargs.get("headers") or {}
                headers.setdefault("Cookie", cookie_header)
                kwargs["headers"] = headers
        return await original_request(method, url, **kwargs)

    client.request = request_with_cookies  # type: ignore[method-assign]
    return client
